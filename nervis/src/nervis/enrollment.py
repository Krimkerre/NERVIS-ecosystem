"""How NERVIS knows a process that wants to register is allowed to (§5.1).

§5.1 requires *"authenticated local dynamic registration"* and, in the same
breath, **"do not accept an unauthenticated process's claimed service type or
endpoint."** Those two sentences are the whole design problem: the Bridge has
to be able to say "I exist, on port 7073" without any process on the machine
being able to say the same thing and be believed.

**The shared secret is a file, and the file's permissions are the
authentication.** NERVIS writes a random secret to a path only its own user can
read (`0600`, in a `0700` directory). A registering process proves it is the
user's by reading it. That is a real check — it is the same one `ssh` makes of
a private key — and it needs no user interaction at all, which matters here
because the alternative was asking somebody to copy a token out of a terminal
window, and a security step that is annoying is a security step that gets
turned off.

What this does **not** prove is *which* program is registering. Any process
running as this user can read the file, so the claim "I am the Clarvis Bridge"
is trusted exactly as far as the user's own account is. That is the correct
trust boundary for a localhost control plane on a single-user desktop, and it
is the boundary the ecosystem already assumes everywhere else. It would not be
adequate on a shared machine, and `NERVIS.md` §5.1's threat model says so.
"""

from __future__ import annotations

import logging
import os
import secrets
from hmac import compare_digest
from pathlib import Path

logger = logging.getLogger("nervis.enrollment")

# Long enough that guessing is not a strategy, short enough to paste when
# somebody is debugging by hand.
SECRET_BYTES = 32

# The permissions that carry the whole argument above. A secret at `0644` is
# not a secret, and the failure is silent — everything still works, and the
# check has simply stopped meaning anything.
SECRET_MODE = 0o600
DIRECTORY_MODE = 0o700


class EnrollmentRefusedError(Exception):
    """A registration attempt that did not prove it was local and authorised."""


def secret_path(database_path: str) -> Path:
    """Beside the database, because they have the same lifetime and owner.

    Deriving it from the database path rather than adding a setting means the
    two cannot be configured apart — a secret that outlives the registry it
    authenticates is a credential nobody remembers issuing.
    """
    return Path(database_path).expanduser().resolve().with_suffix(".enrollment")


def load_or_create(database_path: str) -> str:
    """The enrollment secret, generated on first run.

    Created rather than configured. A secret with a default value is not one,
    and a secret an operator must invent is one that ends up as `changeme`.
    """
    path = secret_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    if path.exists():
        _refuse_if_world_readable(path)
        return path.read_text(encoding="utf-8").strip()

    secret = secrets.token_urlsafe(SECRET_BYTES)
    # Written through `os.open` with the mode in the *creation* call, not
    # `write_text` followed by `chmod`. The gap between those two is a window
    # where the secret exists at the process umask, and a window that small is
    # still one a watcher on the same machine can lose a race to.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(secret)
    logger.info("wrote a new enrollment secret to %s", path)
    return secret


def _refuse_if_world_readable(path: Path) -> None:
    """Because a secret anyone can read has stopped authenticating anything.

    Refusing to start is deliberate. The tempting alternative — fix the mode
    and carry on — hides the interesting part: the file was readable for some
    unknown length of time, and its contents should be treated as known.
    """
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise EnrollmentRefusedError(
            f"{path} is mode {mode:o} and readable beyond its owner. "
            "Delete it so a new secret is generated; treat the old one as disclosed."
        )


def presented_secret(header: str | None) -> str:
    """The token out of an `Authorization: Bearer …` header, or empty."""
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def matches(presented: str, expected: str) -> bool:
    """Constant-time, because the obvious `==` leaks the secret one byte at a time.

    A registration endpoint is unauthenticated by definition — it is what an
    unauthenticated process calls — so it is exactly the surface where an
    attacker gets unlimited attempts at measuring a comparison.
    """
    if not presented or not expected:
        return False
    return compare_digest(presented, expected)
