"""`/api/v1/link/*` — finding the owner's other computers, and letting them find this one.

The mechanism is `nervis.discovery`; this is the three routes a screen needs. Linking itself
stays where it was: `tools/run.py link add`, in a terminal, because it asks for the other
computer's password once and a web page is the wrong place to type that.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from fastapi import APIRouter, Request

from nervis.discovery import Announcer, find_computers, own_name, tool
from nervis.errors import InvalidConfigurationError
from nervis.launcher import ask_launcher
from nervis.peers.computer import LINK_BACK_NERVIS_PORT
from nervis.storage.database import Database

router = APIRouter(prefix="/api/v1/link", tags=["link"])

#: The setting behind "Let other computers find this one". Not on the exportable list:
#: whether a machine announces itself is a decision about that machine and its network.
FINDABLE_KEY = "link.findable"

#: What this computer is currently asking for, while it waits to be allowed: the other
#: computer's name and address, and whether the link should go both ways. Cleared the moment
#: the link works, so nothing keeps asking after it has been answered.
REQUEST_KEY = "link.request"

#: The computers this one has allowed in, each with the key that was authorised — which is
#: what "stop allowing it" needs later, and what a screen shows so somebody can tell which is
#: which. Authorising itself happens in `~/.ssh/authorized_keys`, through the launcher.
ALLOWED_KEY = "link.allowed"


def _stored(database: Database, key: str, fallback: Any) -> Any:
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(row["value"]) if row else fallback
    except (TypeError, ValueError):
        return fallback


def _store(database: Database, key: str, value: Any) -> None:
    with database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def pending_request(database: Database) -> dict[str, Any]:
    held = _stored(database, REQUEST_KEY, {})
    return held if isinstance(held, dict) else {}


def allowed_computers(database: Database) -> list[dict[str, Any]]:
    held = _stored(database, ALLOWED_KEY, [])
    return [one for one in held if isinstance(one, dict)] if isinstance(held, list) else []


def _announce(request: Request) -> str:
    """Say on the network what this computer is: findable, and whether it is asking to link."""
    database = request.app.state.database
    waiting = pending_request(database)
    key = _stored(database, "link.key", "")
    announcer: Announcer = request.app.state.announcer
    return announcer.sync(
        findable(database),
        {"key": key if isinstance(key, str) else "", "want": str(waiting.get("name", ""))},
    )


def findable(database: Database) -> bool:
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (FINDABLE_KEY,)
    ).fetchone()
    try:
        return bool(row and json.loads(row["value"]) is True)
    except (TypeError, ValueError):
        return False


def linked_in() -> bool:
    """Whether another computer has a link open *into* this one, right now.

    **The two ends of a link do not look the same.** The computer that dialled has the address
    in its settings; the one that was dialled has nothing at all — its evidence is the port the
    other one opened backwards, which exists only while that link is up. Without this, the card
    on the Mac said "not linked to anything" while the ThinkPad was linked into it and its
    settings were being read through that very tunnel.

    A loopback connect, so it costs nothing and cannot be wrong about a remote machine.
    """
    try:
        with socket.create_connection(("127.0.0.1", LINK_BACK_NERVIS_PORT), timeout=0.2):
            return True
    except OSError:
        return False


def _state(request: Request, detail: str = "") -> dict[str, Any]:
    announcer: Announcer = request.app.state.announcer
    database = request.app.state.database
    return {
        "findable": findable(database),
        "announcing": announcer.running,
        "name": own_name(),
        "tool": tool(),
        "detail": detail,
        "waiting_for": pending_request(database),
        "allowed": allowed_computers(database),
        "linked_in": linked_in(),
    }


@router.get("/discover")
def discover() -> dict[str, Any]:
    """Look for other computers announcing NERVIS on this network. Changes nothing.

    A plain `def`, so FastAPI runs it on a worker thread: listening takes a few seconds, and
    doing that on the event loop would stall every other request NERVIS is answering.

    **One search answers both questions a screen has**: which computers are there, and which
    of them are asking to link to *this* one — so the card offers Allow on the rows that are
    asking and Link on the rest, without listening twice.
    """
    me = own_name()
    found = find_computers()
    for one in found.get("computers", []):
        if one.get("want") == me and one.get("key"):
            one["fingerprint"] = ask_launcher(
                "fingerprint", "--key", str(one["key"])).get("fingerprint", "")
    found["name"] = me
    return found


@router.get("/findable")
def read_findable(request: Request) -> dict[str, Any]:
    """Whether this computer is announcing itself, and under what name."""
    return _state(request)


@router.put("/findable")
async def write_findable(request: Request) -> dict[str, Any]:
    """Turn the announcement on or off, now — not at the next start.

    Stored first and then applied, so what the card shows after a reload is what was asked
    for even if the announcement itself could not start (no Avahi on this Linux, say); the
    sentence explaining why comes back with the answer.
    """
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("value"), bool):
        raise InvalidConfigurationError("body must be {\"value\": true} or {\"value\": false}")
    with request.app.state.database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (FINDABLE_KEY, json.dumps(body["value"])),
        )
    detail = _announce(request)
    return _state(request, detail)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise InvalidConfigurationError("body must be an object")
    return body


@router.post("/request")
async def ask_to_link(request: Request) -> dict[str, Any]:
    """Ask another computer to let this one link to it. Nothing is opened yet.

    **This is the half that replaces typing a password.** Instead of logging in to the other
    computer, this one says on the network what its key is and who it is asking — and the
    person at the other computer presses Allow, where NERVIS is already running as them. The
    fingerprint comes back so both screens can show the same one.
    """
    body = await _body(request)
    address, name = str(body.get("address", "")).strip(), str(body.get("name", "")).strip()
    if not address or not name:
        raise InvalidConfigurationError("both 'address' and 'name' are needed")
    made = ask_launcher("key")
    if not made.get("ok"):
        return {"ok": False, "detail": made.get("detail", "this computer has no link key")}
    database = request.app.state.database
    _store(database, "link.key", made.get("public_key", ""))
    _store(database, REQUEST_KEY, {
        "name": name, "address": address, "inbound": bool(body.get("inbound")),
        "fingerprint": made.get("fingerprint", ""),
    })
    # Findable while asking, or the other computer cannot see the question.
    _store(database, FINDABLE_KEY, True)
    detail = _announce(request)
    return {"ok": True, **_state(request, detail)}


@router.post("/request/cancel")
async def stop_asking(request: Request) -> dict[str, Any]:
    _store(request.app.state.database, REQUEST_KEY, {})
    return {"ok": True, **_state(request, _announce(request))}


@router.get("/requests")
def who_is_asking(request: Request) -> dict[str, Any]:
    """The computers on this network asking to link to *this* one, with their fingerprints.

    A search rather than a mailbox: there is no channel between two computers until one of
    them is allowed in, so asking is something a computer says on the network, and this is
    NERVIS listening for it.
    """
    me = own_name()
    found = find_computers()
    asking = [one for one in found.get("computers", []) if one.get("want") == me and one.get("key")]
    for one in asking:
        named = ask_launcher("fingerprint", "--key", str(one["key"]))
        one["fingerprint"] = named.get("fingerprint", "")
    return {"tool": found.get("tool"), "detail": found.get("detail", ""), "asking": asking,
            "name": me, "allowed": allowed_computers(request.app.state.database)}


@router.post("/approve")
async def allow_a_computer(request: Request) -> dict[str, Any]:
    """Let one computer that is asking open this one's forwarded ports.

    The key is taken from a **fresh** search rather than from the browser, so what is
    authorised is what that computer is announcing at the moment somebody presses Allow.
    Writing it is the launcher's job (`link authorize`), which accepts nothing but an
    ed25519 key and writes its own restrictions around it.
    """
    body = await _body(request)
    name = str(body.get("name", "")).strip()
    asking = who_is_asking(request)["asking"]
    match = next((one for one in asking if one.get("name") == name), None)
    if match is None:
        return {"ok": False, "detail": f"{name or 'that computer'} is not asking to link now"}
    written = ask_launcher("authorize", "--key", str(match["key"]), "--label", name)
    if not written.get("ok"):
        # The one way this fails in practice is a key that did not arrive whole, which means
        # the other computer is announcing it the way the build before 0.34.80 did.
        return {"ok": False, "detail": f"{written.get('detail', 'the key was not written')}"
                                       " — that computer may be running an older NERVIS"}
    database = request.app.state.database
    kept = [one for one in allowed_computers(database) if one.get("key") != match["key"]]
    kept.append({"name": name, "key": match["key"],
                 "fingerprint": written.get("fingerprint", match.get("fingerprint", ""))})
    _store(database, ALLOWED_KEY, kept)
    return {"ok": True, "name": name, "fingerprint": kept[-1]["fingerprint"],
            "already": bool(written.get("already"))}


@router.post("/revoke")
async def stop_allowing(request: Request) -> dict[str, Any]:
    """Stop letting one computer in, and take its key back out of `authorized_keys`."""
    body = await _body(request)
    name = str(body.get("name", "")).strip()
    database = request.app.state.database
    match = next((one for one in allowed_computers(database) if one.get("name") == name), None)
    if match is None:
        return {"ok": False, "detail": f"{name or 'that computer'} is not on the list"}
    removed = ask_launcher("revoke", "--key", str(match.get("key", "")))
    if not removed.get("ok"):
        return {"ok": False, "detail": removed.get("detail", "the key was not removed")}
    _store(database, ALLOWED_KEY,
           [one for one in allowed_computers(database) if one.get("name") != name])
    return {"ok": True, "name": name, "removed": removed.get("removed", 0)}


@router.post("/close")
def close_the_link() -> dict[str, Any]:
    """Close the tunnel now, leaving the rest of the stack running.

    What *Unlink* presses after it has forgotten the address: a link that is no longer wanted
    should stop being open, rather than staying up until the next time somebody stops the
    stack.
    """
    return ask_launcher("close")


@router.post("/check")
def check_the_link(request: Request) -> dict[str, Any]:
    """Try the link this computer is waiting for, and finish it the moment it works.

    Polled by the card while somebody waits for the other computer's Allow. When the tunnel
    opens, the address is saved and the link is opened straight away (`link open`), so
    pairing from the screen never ends with "now restart the stack".
    """
    database = request.app.state.database
    waiting = pending_request(database)
    address = str(waiting.get("address", ""))
    if not address:
        return {"ok": True, "waiting": False, "linked": False,
                "detail": "this computer is not asking to link to anything"}
    inbound = bool(waiting.get("inbound"))
    tried = ask_launcher("test", "--address", address, *(["--inbound"] if inbound else []))
    if not tried.get("connected"):
        return {"ok": True, "waiting": True, "linked": False,
                "detail": f"{waiting.get('name', address)} has not allowed it yet"}
    saved = ask_launcher("save", "--address", address, *(["--inbound"] if inbound else []))
    opened = ask_launcher("open")
    _store(database, REQUEST_KEY, {})
    _announce(request)
    return {"ok": bool(saved.get("ok")), "waiting": False, "linked": True,
            "answering": bool(opened.get("answering")), "address": address,
            "detail": "" if opened.get("answering") else
                      "linked — the other computer's stack is not answering yet"}
