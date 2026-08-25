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

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse, Response

# The dashboard sits beside the package rather than inside it: `src/nervis/` is
# the importable module and `nervis/index.html` is the repository's own file,
# already referenced by `tools/run.py` and by every bookmark anyone has.
DASHBOARD = Path(__file__).resolve().parents[2] / "index.html"


def register_dashboard(api: FastAPI) -> None:
    """Serve the dashboard, and say so plainly when it is missing.

    A missing file is a 404 with an explanation rather than a stack trace: an
    installation running the package without the repository beside it is a real
    configuration, and it should still get a working API.
    """

    @api.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # A redirect rather than serving the page at both paths, so the
        # dashboard has one address. Two URLs for one page is two cache
        # entries, two bookmarks, and one of them going stale.
        return RedirectResponse("/index.html")

    @api.get("/index.html", include_in_schema=False)
    async def dashboard() -> Response:
        if not DASHBOARD.is_file():
            return Response(
                f"No dashboard at {DASHBOARD}. The API is unaffected; see /api/v1/health.",
                status_code=404,
                media_type="text/plain",
            )
        # No caching. The dashboard is edited constantly during development and
        # a cached copy of yesterday's build reporting today's data is the
        # single most confusing failure this project has produced.
        return FileResponse(
            DASHBOARD,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
