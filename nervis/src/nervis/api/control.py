"""The page's own token, and the six routes that require it.

**§16 item 4, one hop up.** RAVIS stopped treating a loopback bind as
authorization: every management mutation there needs an admin credential, and an
anonymous caller gets 403. NERVIS holds that credential and proxies six of those
mutations, so the gate was closed at RAVIS and left open here — anything able to
reach `127.0.0.1:8790` could change RAVIS's configuration while holding nothing.
Moving the credential out of the browser's reach was right; leaving the decision
to *use* it ungated was the half that got missed.

**Why a page token rather than a bearer credential.** RAVIS deliberately did not
build a CSRF token, and `RAVIS.md` §4.4 gives the reason: such a token defends
*ambient* authority, and RAVIS's callers present a bearer header, which a page on
another origin cannot set. NERVIS's dashboard is the opposite case — a
same-origin page carrying no credential, where the browser's willingness to send
the request is itself the authority. So the defence is the one that matches the
threat: a value minted per process, embedded in the page NERVIS serves, and
required back on the six routes.

**What it does and does not stop.** A page on another origin may issue the
request and may not read `/index.html`, so it never learns the token. A local
process that can read the page can already do whatever the page can do, and is
not the attacker this closes — that boundary is the operating system's, and
pretending otherwise would be the "looks encrypted and isn't" mistake §16 item 2
refused to make. Reads stay open, because a console that asked for a credential
before drawing a health table is a console nobody opens.
"""

from __future__ import annotations

import secrets

from fastapi import Request

from nervis.errors import ControlTokenRequiredError

HEADER = "x-nervis-control"


def require_control(request: Request) -> None:
    """Refuse unless the caller presents this process's control token.

    Compared with `compare_digest` rather than `==`. The timing difference is
    not the realistic attack here, and writing the comparison the careful way
    costs nothing and stops the question being asked again.
    """
    held = str(getattr(request.app.state, "control_token", "") or "")
    offered = request.headers.get(HEADER, "")
    if not held or not offered or not secrets.compare_digest(held, offered):
        raise ControlTokenRequiredError(
            "changing RAVIS's configuration through NERVIS needs the dashboard's "
            "control token; reload the dashboard and try again"
        )
