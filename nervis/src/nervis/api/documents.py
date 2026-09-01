"""`/api/v1/documents` — hand back a file NERVIS wrote (M4/M11 writers).

Chat can already write a reply or a whole conversation into the workspace. This
is the other half a person actually wants: **the file, in their browser, in
their Downloads folder.** Until now the answer to "export this conversation" was
a filename and a directory, which is the right thing to have done and the wrong
place to stop — the workspace is on the machine NERVIS runs on, and that is not
always the machine somebody is reading the dashboard from.

**Reads only, and only inside the workspace.** The name goes through
`resolve_in_workspace`, which resolves before it compares — so `../../.ssh/id_rsa`
is refused as a boundary violation rather than served, and the refusal is a
different error from "no such file" because they are different facts.

**It hands back only what NERVIS wrote.** The extension allowlist is not
decoration: a workspace is a directory a person chose, and it may well hold
things they never meant to serve over HTTP. Two suffixes, both of them formats
NERVIS itself produces.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from nervis.errors import NotFoundError, RefusedError
from nervis.workspace import OutsideWorkspaceError, resolve_in_workspace

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

#: What may be handed back, by suffix. The two things `pdf.py` and the document
#: writer produce and nothing else — an allowlist rather than a denylist,
#: because the interesting files in somebody's workspace are the ones nobody
#: thought to forbid.
SERVED = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8"}


@router.get("/{name:path}")
async def read_document(name: str, request: Request) -> FileResponse:
    """One file from the workspace, as a download.

    `Content-Disposition: attachment` rather than letting the browser decide:
    the point of this endpoint is a file landing in Downloads, and a PDF
    rendered in a tab instead is the browser being helpful in the one way
    nobody asked for.
    """
    root = str(getattr(request.app.state.settings, "workspace_path", "") or "").strip()
    if not root:
        raise RefusedError("NERVIS has no workspace configured, so it holds no documents")
    try:
        found = resolve_in_workspace(Path(root), name)
    except OutsideWorkspaceError as refusal:
        # A boundary refusing, not a question about an empty place. Reported as
        # a refusal so it cannot be mistaken for a typo in the name.
        raise RefusedError(str(refusal)) from refusal
    if found.path.suffix.lower() not in SERVED:
        raise RefusedError(
            f"NERVIS serves {', '.join(sorted(SERVED))} from the workspace, "
            f"and {found.shown} is neither"
        )
    if not found.path.is_file():
        raise NotFoundError(f"no document named {found.shown}")
    return FileResponse(
        found.path,
        media_type=SERVED[found.path.suffix.lower()],
        filename=found.path.name,
        content_disposition_type="attachment",
    )
