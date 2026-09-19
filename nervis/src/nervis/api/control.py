"""The page's own token, and the routes that require it.

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

import re
import secrets

from fastapi import Request

from nervis.errors import ControlTokenRequiredError

HEADER = "x-nervis-control"


#: Methods that change nothing, and so never need the token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Writes under `/api/v1/` that are not the page's to make, so the page's token cannot be asked of
#: them — each guarded its own way — and the one negotiated read that is a POST.
#:
#: * Events and registration come from SIRVIS, RAVIS and each Clarvis Bridge; registration needs
#:   the enrollment secret (`instances._require_enrollment`), and a registered Bridge's heartbeat
#:   and deregistration need the token it was issued. Event delivery needs a sender's
#:   credential — a service's events secret from the launcher, or a registered window's token
#:   (`events._senders`), which closed `design/security/review-2026-09-16.md` S7.
#: * SIRVIS's recommendations are a read with a body (§14.3), passed to SIRVIS and stored nowhere.
#:
#: Everything else under `/api/v1/` that is not a read needs the token, and a route added later
#: does too without having to remember it.
NOT_THE_PAGES = (
    re.compile(r"^/api/v1/events$"),
    re.compile(r"^/api/v1/registry/instances(?:/|$)"),
    re.compile(r"^/api/v1/sirvis/recommendations$"),
)


def needs_control(method: str, path: str) -> bool:
    """Whether a request must carry the page's control token (NERVIS 0.34.17)."""
    return (
        method.upper() not in SAFE_METHODS
        and path.startswith("/api/v1/")
        and not any(pattern.match(path) for pattern in NOT_THE_PAGES)
    )


def presents_control(request: Request) -> bool:
    """Whether `request` carries this process's control token.

    Compared with `compare_digest` rather than `==`. The timing difference is
    not the realistic attack here, and writing the comparison the careful way
    costs nothing and stops the question being asked again.
    """
    held = str(getattr(request.app.state, "control_token", "") or "")
    offered = request.headers.get(HEADER, "")
    return bool(held and offered and secrets.compare_digest(held, offered))


def control_refusal() -> ControlTokenRequiredError:
    return ControlTokenRequiredError(
            "this change through NERVIS needs the dashboard's control token, which "
            "changes whenever NERVIS restarts; reload the dashboard and try again"
        )


def require_control(request: Request) -> None:
    """Refuse unless the caller presents this process's control token.

    Kept on the routes that had it before the middleware rule (`needs_control`) covered every
    write, so a route stays guarded even if it ever moved out from under `/api/v1/`.
    """
    if not presents_control(request):
        raise control_refusal()
