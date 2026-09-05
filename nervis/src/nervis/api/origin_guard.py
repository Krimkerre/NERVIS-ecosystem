"""Whether a state-changing request came from somewhere that is not this page.

**Found reverifying §15's safety-gate line, and it is a different hole than the
one that line was about.** The control token stops a page with no credential
from driving RAVIS's six proxied mutations; every *other* mutating route in
this service — supervision, chat, background jobs, settings import, and more —
checked nothing at all. `app.py`'s own module docstring argued this was fine:
*"NERVIS serves the dashboard and the dashboard's own API from one origin, so
its requests are same-origin and CORS never enters the picture... a header
nobody's browser ever sends a request past."* That reasoning has a hole in it:
CORS response headers govern whether a page's script may *read* a response,
never whether the browser sends the request or whether the server acts on it.
A page on another origin can `fetch()` this service with a body shaped like a
"simple request" — `Content-Type: text/plain`, no custom headers — which
triggers no preflight and is sent regardless of what NERVIS would have
answered. And every mutating route here parses its body with
`json.loads(await request.body())`, not through anything that reads
`Content-Type` — so a `text/plain` body containing JSON is accepted exactly
like one declared as JSON. Verified live: a forged `Origin` and a `text/plain`
body flipped the supervision switch and wrote an attacker-chosen executable and
argument list into the adapter table, through the real app, with nothing
refusing it.

**A different threat than `HostRejectedError`'s, and complementary rather than
redundant.** The Host check (`app.py`) catches a page whose *own* hostname has
been re-pointed at this machine — the browser still calls that same-origin, and
only the literal `Host` string gives it away; an `Origin` check has nothing to
reject there; because the browser can omit `Origin` on a same-origin request
under that hostname. This catches the ordinary case that check cannot: a page
genuinely served from `https://evil.example`, making an ordinary cross-origin
request to a real address, no rebinding involved — which is exactly the shape
of the request proved live against this codebase.

**Sec-Fetch-Site first, `Origin` as the fallback, and absence of both is not a
refusal.** Every browser that implements Fetch Metadata — effectively all of
them now — sets `Sec-Fetch-Site` on every request and a page cannot override
it, which makes it the more reliable signal: `cross-site` is unambiguous.
Where it is absent (an older browser, or a non-browser caller), `Origin` is the
fallback the web already uses for the same purpose. Where *both* are absent,
the caller is not a browser subject to fetch metadata or CORS at all — the
existing test suite's `TestClient`, another local process, this same machine's
own tooling — and refusing those would defend against a threat that only a
browser can create, using a signal only a browser sends. That gap is the same
one `nervis/src/nervis/api/control.py`'s own docstring already accepts for the
control token: a co-resident process is an operating-system boundary, stated
rather than pretended otherwise, and this file is not the one that closes it.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Requests that cannot mutate anything, and so carry nothing to refuse. `PUT`,
#: `PATCH`, `POST` and `DELETE` are the shapes every route in this service uses
#: to change something; `OPTIONS` is a preflight the browser sends on its own
#: behalf, never a page's, and refusing it would break the preflight the
#: browser needs to decide whether to send the real request at all.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: `Sec-Fetch-Site` values a same-origin (or non-cross-origin) request carries.
#: `none` is not this page calling itself — it is a request with no initiator
#: at all, such as a browser's own address-bar navigation — and is at least as
#: trustworthy as same-origin for a request nothing else could have triggered.
NOT_CROSS_SITE = frozenset({"same-origin", "same-site", "none"})


def expected_origins(served_hosts: Mapping[str, str] | list[str], port: int) -> frozenset[str]:
    """Every `Origin` value a legitimate same-origin request could carry.

    Built from `served_hosts` — the same allow-list `HostRejectedError`'s check
    already reads — rather than the single address NERVIS happens to have been
    started on, because an operator may reach this dashboard by `127.0.0.1`,
    `localhost` or `::1` interchangeably and a real one of those is not an
    attacker for using a name this process already answers to.
    """
    origins = set()
    for host in served_hosts:
        name = host.strip("[]")
        # An IPv6 literal needs brackets in a URL authority (`http://[::1]:port`),
        # a name or IPv4 literal must not have them — `http://[localhost]:port`
        # is not a value any browser would ever send.
        authority = f"[{name}]" if ":" in name else name
        origins.add(f"http://{authority}:{port}".lower())
    return frozenset(origins)


def refuses_cross_origin_mutation(
    method: str, headers: Mapping[str, str], expected: frozenset[str]
) -> bool:
    """Whether this request must be refused as a cross-origin mutation.

    `headers` keys are expected lower-cased, which is how `Request.headers`
    already behaves and how every test below constructs one directly.
    """
    if method.upper() in SAFE_METHODS:
        return False

    sec_fetch_site = headers.get("sec-fetch-site")
    if sec_fetch_site is not None:
        return sec_fetch_site.lower() not in NOT_CROSS_SITE

    origin = headers.get("origin")
    if origin is not None:
        return origin.lower() not in expected

    # Neither header is present: not a browser request subject to fetch
    # metadata, and not one CORS or this check has any business judging.
    return False
