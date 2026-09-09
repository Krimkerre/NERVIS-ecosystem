"""`/api/v1/workspace/entries` — browsing and moving files inside the workspace.

**Why NERVIS grows a file manager at all.** The workspace has four rooms and
every one of them is filled or emptied by something else: chat uploads into
`import`, chat writes into `export`, the editor owns `clarvis`, and `library`
is the room a person fills by hand — which until now meant leaving the
dashboard, finding the directory, and coming back. On a machine running the
stack full-screen that is the only remaining reason to switch applications.

**What it is not.** Not a filesystem browser. Every path here goes through
`resolve_in_workspace`, so the workspace root is the ceiling and a climb out is
refused rather than normalised — the same wall chat reads through, for the same
reason: a boundary that lives in a prompt is one the prompt can argue with, and
this one is a path comparison.

**Two rules on top of containment**, both about not letting a convenience break
something that depends on the layout:

- *The rooms are structural.* `import`, `export`, `library` and `clarvis` can be
  filled and emptied and cannot themselves be renamed, moved or deleted. Every
  other part of NERVIS resolves files by room name; a manager that let somebody
  rename `export` would leave chat writing into a directory nothing reads.
- *Dot-directories are not the manager's business.* `.attachments` belongs to
  conversations and `.trash` belongs to this module. They are neither listed nor
  writable here — an attachment moved out from under its conversation is a file
  that still exists and a reading that has silently changed.

**Delete moves to `.trash`.** Every other mutation in this service is a model
proposing and a person agreeing; this is a person acting, like the upload route,
and that is fine. What is not fine is an irreversible delete two clicks deep in
a browser tab over the room somebody keeps files in. The trash is swept on the
same fortnight timer as attachments, so "gone" is recoverable for two weeks and
then actually gone.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response

from nervis import workspace
from nervis.api.control import require_control
from nervis.documents import readable_name
from nervis.errors import InvalidConfigurationError, NotFoundError, RefusedError
from nervis.workspace import OutsideWorkspaceError, resolve_in_workspace, unmounted

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])

#: Where a deleted entry waits out its fortnight.
TRASH = ".trash"

#: How long it waits. The same window attachments get, because it is the same
#: judgement: long enough to notice a mistake, short enough that a directory
#: nobody opens does not become an archive nobody meant to keep.
TRASH_SECONDS = 14 * 24 * 60 * 60.0


#: What the workspace is called when a place has to be named.
HOME = "workspace"


def places(settings: Any) -> dict[str, Path]:
    """Every root the Files tab may reach, by name.

    **The containment argument survives having more than one root**, and it is
    worth saying how. Every path is still resolved and then compared, and the
    comparison is still against a directory nobody in a request chose — it is
    now "inside one of the roots an operator configured" rather than "inside
    the workspace". What is never possible is a root that came from a request.

    A configured place that is not there is left out rather than created. A NAS
    that is asleep is a place that is unreachable today, and a manager that
    made `/Volumes/nas` locally to have somewhere to put things would be
    writing to the machine's own disk under a name that says otherwise.
    """
    found: dict[str, Path] = {}
    home = str(getattr(settings, "workspace_path", "") or "").strip()
    if home:
        found[HOME] = Path(home).expanduser()
    for pair in str(getattr(settings, "file_places", "") or "").split(","):
        name, _, where = pair.partition("=")
        name, where = name.strip(), where.strip()
        if not name or not where or name == HOME:
            continue
        place = Path(where).expanduser()
        if place.is_dir() and not unmounted(place):
            found[name] = place
    return found


def _root(request: Request, place: str = "") -> Path:
    """One named place, or the refusal that says why there is not one."""
    known = places(request.app.state.settings)
    if not known:
        raise InvalidConfigurationError(
            "NERVIS has no workspace configured, so there are no files to manage. "
            "Set NERVIS_WORKSPACE_PATH to the directory it may read and write."
        )
    wanted = place or HOME
    if wanted not in known:
        raise NotFoundError(
            f"{wanted!r} is not a place NERVIS knows. It reaches "
            f"{', '.join(sorted(known))}."
        )
    return known[wanted]


def _inside(root: Path, candidate: str) -> Path:
    """One path inside the workspace, or a refusal — the wall, in one place."""
    try:
        return resolve_in_workspace(root, candidate or ".").path
    except OutsideWorkspaceError as refusal:
        raise RefusedError(str(refusal)) from refusal


def _hidden(relative: Path) -> bool:
    """Whether a path belongs to something other than this manager.

    Any segment beginning with a dot: `.attachments` is a conversation's, and
    `.trash` is this module's own. Checked per segment rather than on the first
    one, because `library/.attachments` would otherwise be a way to keep files
    somewhere the manager refuses to look at but happily writes into.
    """
    return any(part.startswith(".") for part in relative.parts)


def _structural(root: Path, target: Path, place: str = HOME) -> bool:
    """Whether this is one of the workspace's own rooms.

    Rooms are referred to by name from the rest of the service — chat writes to
    `export`, the editor opens `clarvis` — so they may be filled and emptied and
    may not be renamed away from under those callers.

    **Only in the workspace.** A share's top level is somebody's own directory;
    refusing to make a folder there would be NERVIS imposing its layout on a
    NAS it was merely given access to.
    """
    return place == HOME and target.parent == root.resolve(strict=False)


def _entry(path: Path, root: Path) -> dict[str, Any]:
    """One row, in the shape the screen renders."""
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(root.resolve(strict=False))),
        "kind": "folder" if path.is_dir() else "file",
        "bytes": 0 if path.is_dir() else stat.st_size,
        "modified": stat.st_mtime,
        # What the download route will actually serve, answered here rather
        # than guessed from a suffix list the screen keeps in step by hand —
        # the mistake `routes.py` records having made once already.
        "readable": readable_name(path.name) if path.is_file() else False,
    }


#: The rooms, in the order they mean something: what arrives, what is kept,
#: what is produced, what the editor opens. Alphabetical would put `clarvis`
#: first, which is the one a person visits least.
ROOMS = (workspace.IMPORT, workspace.LIBRARY, workspace.EXPORT, "clarvis")


def _ordering(row: dict[str, Any], at_top: bool) -> tuple[Any, ...]:
    """How one row sorts against the others.

    **Folders are not sorted by time.** A directory's timestamp moves whenever
    anything inside it does, so "newest folder" ranks by whichever room was
    touched last — which is noise presented as an order. Folders sort by name,
    and at the top of the workspace by what they are for.

    Files do sort by time, newest first: somebody who just saved or dropped
    something is looking for the thing they just saved or dropped.
    """
    if row["kind"] == "folder":
        place = ROOMS.index(row["name"]) if at_top and row["name"] in ROOMS else len(ROOMS)
        return (0, place, row["name"].lower())
    return (1, 0, -float(row["modified"]))


@router.get("/entries")
async def list_entries(request: Request) -> dict[str, Any]:
    """One directory inside the workspace, folders first.

    Answers with an empty list and a reason rather than an error when the
    workspace is unconfigured — the screen needs to say "turn this on", and a
    404 makes an install that was never asked to read files look broken.
    """
    place = str(request.query_params.get("place") or HOME)
    try:
        root = _root(request, place)
    except InvalidConfigurationError as off:
        return {"items": [], "path": "", "place": HOME, "workspace": "", "detail": str(off)}

    where = str(request.query_params.get("path") or "")
    target = _inside(root, where)
    base = root.resolve(strict=False)
    if _hidden(Path(str(target.relative_to(base)) if target != base else ".")):
        raise RefusedError("that directory belongs to a conversation, not to the file manager")
    if not target.is_dir():
        raise NotFoundError(f"there is no directory {where or '.'} in the workspace")

    rows = [
        _entry(child, root)
        for child in target.iterdir()
        if not child.name.startswith(".")
    ]
    rows.sort(key=lambda row: _ordering(row, at_top=target == base and place == HOME))
    return {
        "items": rows,
        "path": str(target.relative_to(base)) if target != base else "",
        "place": place,
        "workspace": str(base),
        # Rooms are the *workspace's* structure. A share is somebody else's
        # directory and NERVIS has no opinion about what is in it.
        "rooms": list(ROOMS) if place == HOME else [],
        "places": sorted(places(request.app.state.settings)),
        "detail": "",
    }


@router.put("/entries/{path:path}")
async def upload_entry(path: str, request: Request) -> dict[str, Any]:
    """Put a file where the person dropped it.

    Raw body rather than multipart, the same shape the attachment upload uses
    and for the same reason: one filename and some bytes need no dependency.
    """
    require_control(request)
    place = str(request.query_params.get("place") or HOME)
    root = _root(request, place)
    target = _inside(root, path)
    base = root.resolve(strict=False)
    if _hidden(target.relative_to(base)):
        raise RefusedError("that directory belongs to a conversation, not to the file manager")
    if target == base or _structural(root, target, place):
        raise RefusedError("a file goes inside a room, not beside them")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await request.body())
    return {"entry": _entry(target, root)}


@router.post("/folders", status_code=201)
async def make_folder(request: Request) -> dict[str, Any]:
    """A new directory, inside a room."""
    require_control(request)
    body = await request.json() if await request.body() else {}
    place = str(body.get("place") or HOME)
    root = _root(request, place)
    target = _inside(root, str(body.get("path") or ""))
    base = root.resolve(strict=False)
    if target == base:
        raise InvalidConfigurationError("a folder needs a name")
    if _hidden(target.relative_to(base)):
        raise RefusedError("that name belongs to a conversation, not to the file manager")
    if _structural(root, target, place):
        raise RefusedError(
            "the workspace's own rooms are made by NERVIS; new folders go inside them"
        )
    if target.exists():
        raise RefusedError(f"{target.name} is already there")
    target.mkdir(parents=True)
    return {"entry": _entry(target, root)}


@router.post("/move")
async def move_entry(request: Request) -> dict[str, Any]:
    """Move or rename one entry. Both ends are inside the workspace."""
    require_control(request)
    body = await request.json() if await request.body() else {}
    # **Each end names its own place, so a move can cross them.** Copying a
    # document from a room to the NAS is the reason this tab reaches more than
    # one root at all, and both ends are still resolved inside a directory an
    # operator configured — a request chooses *which* place, never where a
    # place is.
    from_place = str(body.get("from_place") or HOME)
    to_place = str(body.get("to_place") or from_place)
    from_root, to_root = _root(request, from_place), _root(request, to_place)
    source = _inside(from_root, str(body.get("from") or ""))
    target = _inside(to_root, str(body.get("to") or ""))
    from_base, to_base = from_root.resolve(strict=False), to_root.resolve(strict=False)
    _refuse_bad_move(source, from_base, target, to_base, from_root, from_place)
    target.parent.mkdir(parents=True, exist_ok=True)
    # **Copy leaves the original, move does not**, and across a share the
    # difference matters more than it does locally: copying to a NAS is the
    # ordinary thing to want, and a "move" that emptied the local room because
    # somebody dragged rather than clicked would be a bad surprise at 38ms a
    # write.
    if bool(body.get("copy")):
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    else:
        shutil.move(str(source), str(target))
    return {"entry": _entry(target, to_root), "place": to_place}


def _refuse_bad_move(
    source: Path, from_base: Path, target: Path, to_base: Path,
    from_root: Path, from_place: str,
) -> None:
    """Every reason a move must not happen, in one place.

    Lifted out of the route when crossing places took it past ruff's complexity
    ceiling, and it is the right seam: everything here is a refusal, and none of
    it is about moving anything.
    """
    for end, base in ((source, from_base), (target, to_base)):
        if end == base or _hidden(end.relative_to(base)):
            raise RefusedError("that path is not one the file manager may touch")
    if _structural(from_root, source, from_place):
        raise RefusedError(
            f"{source.name} is one of the workspace's rooms; the rest of NERVIS "
            "refers to it by name, so it stays where it is"
        )
    if not source.exists():
        raise NotFoundError(f"there is no {source.name} to move")
    if target.exists():
        raise RefusedError(f"{target.name} is already there")
    # **A directory cannot be moved inside itself.** `shutil.move` would happily
    # start and leave both ends broken; `is_relative_to` answers it first.
    if source.is_dir() and target.is_relative_to(source):
        raise RefusedError("a folder cannot be moved inside itself")


@router.delete("/entries/{path:path}")
async def delete_entry(path: str, request: Request) -> dict[str, Any]:
    """Move one entry to the trash, where it waits a fortnight.

    Not `unlink`. A person clicking delete in a browser over the room they keep
    files in should be able to be wrong about it for a couple of weeks.
    """
    require_control(request)
    place = str(request.query_params.get("place") or HOME)
    root = _root(request, place)
    source = _inside(root, path)
    base = root.resolve(strict=False)
    if source == base or _hidden(source.relative_to(base)):
        raise RefusedError("that path is not one the file manager may touch")
    if _structural(root, source, place):
        raise RefusedError(
            f"{source.name} is one of the workspace's rooms; empty it rather than "
            "deleting it"
        )
    if not source.exists():
        raise NotFoundError(f"there is no {source.name} to delete")

    bin_ = base / TRASH
    bin_.mkdir(parents=True, exist_ok=True)
    # Stamped, so deleting two files of the same name a week apart does not make
    # the second one clobber the first one's only remaining copy.
    resting = bin_ / f"{int(time.time())}-{source.name}"
    shutil.move(str(source), str(resting))
    return {"deleted": str(source.relative_to(base)), "recoverable_until_days": 14}


def prune_trash(root: Path, now: float) -> int:
    """Drop trashed entries past their fortnight. Returns how many went.

    Swept on the same timer as attachments, from `app.py`, because they are the
    same promise: kept long enough to undo a mistake, not long enough to become
    an archive nobody chose to keep.
    """
    bin_ = root.expanduser().resolve(strict=False) / TRASH
    if not bin_.is_dir():
        return 0
    gone = 0
    for entry in bin_.iterdir():
        stamp, _, _ = entry.name.partition("-")
        try:
            aged = now - float(stamp)
        except ValueError:
            # A name this module did not write. Left alone rather than guessed
            # about: deleting something because its name is unfamiliar is the
            # opposite of what a trash directory is for.
            continue
        if aged < TRASH_SECONDS:
            continue
        shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(
            missing_ok=True
        )
        gone += 1
    return gone


@router.get("/trash")
async def list_trash(request: Request) -> dict[str, Any]:
    """What is waiting to be forgotten, and what it was called.

    Readable so that "I deleted it by mistake" has an answer that is not "look
    in a hidden directory with a shell".
    """
    root = _root(request)
    bin_ = root.resolve(strict=False) / TRASH
    if not bin_.is_dir():
        return {"items": []}
    items = []
    for entry in sorted(bin_.iterdir(), reverse=True):
        stamp, _, name = entry.name.partition("-")
        try:
            when = float(stamp)
        except ValueError:
            continue
        items.append({
            "name": name,
            "path": f"{TRASH}/{entry.name}",
            "deleted_at": when,
            "kind": "folder" if entry.is_dir() else "file",
        })
    return {"items": items}


@router.post("/trash/restore")
async def restore_from_trash(request: Request) -> dict[str, Any]:
    """Put one trashed entry back where the caller says it belongs."""
    require_control(request)
    root = _root(request)
    body = await request.json() if await request.body() else {}
    base = root.resolve(strict=False)
    resting = _inside(root, str(body.get("path") or ""))
    if resting.parent != base / TRASH or not resting.exists():
        raise NotFoundError("that is not something waiting in the trash")
    target = _inside(root, str(body.get("to") or ""))
    if target == base or _hidden(target.relative_to(base)) or _structural(root, target):
        raise RefusedError("a restored file goes inside a room")
    if target.exists():
        raise RefusedError(f"{target.name} is already there")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(resting), str(target))
    return {"entry": _entry(target, root)}


#: What may be handed to the browser to *render* rather than to save, and the
#: type it is handed as.
#:
#: **An allowlist, and the exclusions are the point.** Anything served inline
#: runs in NERVIS's own origin, so `text/html` and `image/svg+xml` are absent
#: deliberately: both can carry script, and a file somebody dropped into a room
#: is not something NERVIS should let execute next to its own control token.
#: Everything here is inert — a PDF is opened by the browser's viewer, an image
#: is decoded, and text is text because it is served as `text/plain` rather
#: than as whatever its suffix suggests.
INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".json": "text/plain; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8",
}


def viewable(name: str) -> str:
    """The media type this file may be shown as, or empty if it may not."""
    return INLINE_TYPES.get(Path(name).suffix.lower(), "")


@router.get("/download/{path:path}")
async def download_entry(path: str, request: Request) -> Response:
    """The bytes — saved by default, shown in a tab when asked and allowed.

    Separate from `/api/v1/documents/{name}` because that route serves a fixed
    set of suffixes chat can talk about; a file manager shows what is there, and
    what is there is whatever somebody put in the room.

    `?inline=1` asks for the browser's own viewer, which is the difference
    between "a PDF landed in Downloads" and "a PDF opened". It is granted only
    for the types above, and a request for it on anything else is answered with
    the download rather than refused — the file is still what the person asked
    for, and a 409 over a *presentation* preference would be a boundary
    pretending to be one.
    """
    root = _root(request, str(request.query_params.get("place") or HOME))
    target = _inside(root, path)
    base = root.resolve(strict=False)
    if _hidden(target.relative_to(base)) or not target.is_file():
        raise NotFoundError(f"there is no file {path} in the workspace")
    shown = viewable(target.name) if request.query_params.get("inline") else ""
    return Response(
        target.read_bytes(),
        media_type=shown or "application/octet-stream",
        headers={
            "content-disposition":
                f'{"inline" if shown else "attachment"}; filename="{target.name}"',
            # Belt and braces on the same decision: even for an allowed type,
            # the browser is told not to re-interpret it as something else.
            "x-content-type-options": "nosniff",
            # And nothing served out of a room may frame or be framed into
            # something that acts on NERVIS's behalf.
            "content-security-policy": "sandbox; frame-ancestors 'self'",
        },
    )
