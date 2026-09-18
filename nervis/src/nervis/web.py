"""Serving the dashboard.

**M0's "web shell" is the existing `nervis/index.html`, served rather than
rewritten.** That file is the dashboard: it has been built and corrected screen
by screen against two live services, and replacing it with a server-rendered
skeleton to satisfy a milestone's wording would throw away working software to
produce a worse version of it.

What changes at M0 is who serves it. Until now a static file server did, which
is why conversations lived in `localStorage` and Settings had nothing to write
to. From here it comes from the service that also owns a database.

§3 names HTMX and server-rendered HTML as the frontend approach. That remains
the direction for screens NERVIS itself supplies data for, from M1 onward. It
is not a reason to discard the one that exists.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

# The dashboard sits beside the package rather than inside it: `src/nervis/` is
# the importable module and `nervis/index.html` is the repository's own file,
# already referenced by `tools/run.py` and by every bookmark anyone has.
DASHBOARD = Path(__file__).resolve().parents[2] / "index.html"

#: Replaced with this process's control token when the page is served. Left as
#: itself in the file on disk, so the repository's copy is still a page anybody
#: can open directly and nothing in git ever holds a live token.
CONTROL_PLACEHOLDER = "__NERVIS_CONTROL_TOKEN__"


def register_dashboard(api: FastAPI) -> None:
    """Serve the dashboard, and say so plainly when it is missing.

    A missing file is a 404 with an explanation rather than a stack trace: an
    installation running the package without the repository beside it is a real
    configuration, and it should still get a working API.
    """

    # **GET and HEAD, both.** A HEAD is how a Linux desktop decides what a link is before opening
    # it — `gio open`, which Python's `webbrowser` uses under GNOME, asks for the page's type
    # first — and a 405 to that meant the launcher's "open the dashboard" opened nothing on Linux,
    # while gio's error printed the address, token and all, into the launcher's log. Found on the
    # Ubuntu desktop test, 18 September 2026. HTTP has HEAD answer as GET does, without the body;
    # the server drops the body, so the token in it goes nowhere.
    @api.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def root() -> RedirectResponse:
        # A redirect rather than serving the page at both paths, so the
        # dashboard has one address. Two URLs for one page is two cache
        # entries, two bookmarks, and one of them going stale.
        return RedirectResponse("/index.html")

    @api.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
    async def dashboard(request: Request) -> Response:
        if not DASHBOARD.is_file():
            return Response(
                f"No dashboard at {DASHBOARD}. The API is unaffected; see /api/v1/health.",
                status_code=404,
                media_type="text/plain",
            )
        # **Served as text, not as a file, because the page carries a value.**
        # `api/control.py` mints a control token per process and the six RAVIS
        # configuration proxies require it back; the page is where it is handed
        # over, because a page on another origin may request this document and
        # may not read it. `FileResponse` cannot do that, so the substitution
        # happens here — the placeholder is the only edit, and a repository
        # checkout with no NERVIS running still opens as an ordinary file.
        page = DASHBOARD.read_text(encoding="utf-8").replace(
            CONTROL_PLACEHOLDER, str(getattr(request.app.state, "control_token", "")),
        )
        # No caching. The dashboard is edited constantly during development and
        # a cached copy of yesterday's build reporting today's data is the
        # single most confusing failure this project has produced. It matters
        # twice over now: a cached page holds a token a restart has retired.
        return Response(
            page,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
