"""Asking the launcher to do the SSH parts of linking two computers.

Pairing from the screen means somebody presses *Link to this computer* on one and *Allow* on
the other, and no password is typed anywhere. The steps in between are all SSH: make a key,
write one restricted line into `authorized_keys`, open a tunnel and see whether the far end
answers, remember the address.

**None of that is reimplemented here.** `tools/run.py` already owns every one of those facts
— where the key lives, what a restriction line may say, which ports the tunnel forwards, how
the running link is recorded so `stop` finds it — so NERVIS runs that script with `--json`
and reads the answer. A service with its own second opinion about any of it is how two
copies of one rule start to disagree.

The launcher's path arrives in `NERVIS_LAUNCHER`, set by the launcher itself when it starts
NERVIS. A NERVIS started another way (a test, someone running `nervis serve` by hand) has no
launcher, and says so rather than guessing at a path.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Set by `tools/run.py` when it starts NERVIS.
LAUNCHER_ENV = "NERVIS_LAUNCHER"

#: Long enough for the slowest of these: `link test`, which opens the tunnel and waits for the
#: other computer's stack to answer — and which is bounded by the launcher's own timeouts.
LAUNCHER_TIMEOUT_SECONDS = 45.0

NO_LAUNCHER = ("this NERVIS was not started by the launcher, so it cannot do the SSH part — "
               "run tools/run.py link add from a terminal instead")


def launcher_path() -> Path | None:
    """Where `tools/run.py` is, if this NERVIS was started by it."""
    named = os.environ.get(LAUNCHER_ENV, "").strip()
    if not named:
        return None
    path = Path(named)
    return path if path.is_file() else None


def ask_launcher(*arguments: str, timeout: float = LAUNCHER_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run one `link` command and return what it answered. Never raises.

    Every failure — no launcher, a crash, a timeout, something that is not JSON — comes back
    in the same shape as a refusal, because every caller here is answering a screen and a
    screen needs one shape.
    """
    launcher = launcher_path()
    if launcher is None:
        return {"ok": False, "detail": NO_LAUNCHER}
    command = [sys.executable, str(launcher), "link", *arguments, "--json"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as trouble:
        logger.warning("launcher %s did not finish: %s", arguments[:1], trouble)
        return {"ok": False, "detail": f"the launcher did not finish: {trouble}"}
    try:
        answer = json.loads(done.stdout or "{}")
    except ValueError:
        said = (done.stderr or done.stdout or "").strip().splitlines()
        return {"ok": False, "detail": said[-1] if said else "the launcher said nothing"}
    if not isinstance(answer, dict):
        return {"ok": False, "detail": "the launcher answered with something unexpected"}
    return answer
