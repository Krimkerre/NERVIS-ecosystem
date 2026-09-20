"""The other laptop's NERVIS, read through the link the launcher opens.

This is the one peer in this package that is not another *service* but another *machine*:
the owner's second computer, running its own whole stack. It is reached the same way
SIRVIS's models are — through the SSH tunnel `tools/run.py` opens when the link is switched
on — which is why nothing here dials a network address and nothing here holds a credential.
The address is always loopback, and the SSH key at the far end decides who may use it.

**Reads only, and only settings for now.** `/api/v1/settings/export` is a read, so it
crosses the link without the far machine's control token, which lives on that machine and
belongs to its dashboard. Anything that *writes* happens on this side, through the same
`import_settings` an exported file goes through, so the allowlist that decides what may
cross is enforced in exactly one place for both.

**A pull somebody asks for, never a sync.** Two machines quietly mirroring each other have
to answer "what happens when one of them deletes something", and both answers are bad: a
deletion that comes back, or a deletion that spreads. Asking, previewing and applying has
neither, and the link is only up while both stacks are running anyway.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx

from nervis.storage.database import Database

#: Where the other laptop's NERVIS appears on this machine once the link is up — **two places,
#: because only one of the two machines dials**.
#:
#: The laptop that opens the connection gets the other's NERVIS on `18790` (its own forward).
#: The laptop that *accepts* it gets the other's on `8791`, which the dialling machine opened
#: backwards. Neither knows which it is without looking, and it does not matter: both are
#: loopback, both exist only while the link is up, so asking each in turn is the whole answer.
#: (Found on the Mac, 20 September 2026: it accepts rather than dials, so a pull that only ever
#: looked at `18790` would have been dead on exactly the machine it is used from most.)
#:
#: **The same numbers as `tools/run.py`'s `LINK_LOCAL_NERVIS_PORT` and `LINK_BACK_NERVIS_PORT`**,
#: which are what forward them. Two files is one more than ideal, and the alternative — a service
#: reading the launcher — is worse; `test_peer_laptop.py` fails if they ever stop agreeing.
LINK_NERVIS_PORT = 18790
LINK_BACK_NERVIS_PORT = 8791
LINK_NERVIS_URL = f"http://127.0.0.1:{LINK_NERVIS_PORT}"
PEER_URLS = (LINK_NERVIS_URL, f"http://127.0.0.1:{LINK_BACK_NERVIS_PORT}")

#: How long the other laptop has to answer. Short on purpose: this backs a screen, the tunnel
#: is loopback on both sides, and a laptop that is asleep should read as absent in a moment
#: rather than hold a request open for as long as a network timeout would.
PEER_TIMEOUT_SECONDS = 5.0


def linked_peer(database: Database) -> dict[str, Any]:
    """What this machine's Settings screen says about the other laptop.

    The same row the launcher reads before it starts anything (`link.peer`), read here so a
    screen can say *which* machine a pull would reach rather than "the other one".
    """
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = 'link.peer'"
    ).fetchone()
    stored: Any = None
    if row:
        try:
            stored = json.loads(row["value"])
        except (TypeError, ValueError):
            stored = None
    if not isinstance(stored, dict):
        return {"enabled": False, "address": "", "inbound": False}
    return {
        "enabled": bool(stored.get("enabled")),
        "address": str(stored.get("address") or "").strip(),
        "inbound": bool(stored.get("inbound")),
    }


def peer_settings(urls: tuple[str, ...] = PEER_URLS) -> tuple[dict[str, Any] | None, str]:
    """The other laptop's exported settings, or nothing and why not.

    Both addresses are tried, in order, because which one carries the link depends on which
    machine dialled (`PEER_URLS`). A closed port on loopback refuses at once, so the machine
    that has no link pays two instant refusals rather than a wait.

    Never raises: every way this can fail — the link not open, the far stack not started, a
    NERVIS too old to export — is an ordinary state of somebody's second laptop, and a screen
    that had to catch exceptions to say "it is asleep" would be the wrong shape for all of them.
    The sentence returned is the *last* real refusal, or the plain "nothing answered" when
    nothing was listening at either address.
    """
    trouble = ("the other laptop did not answer — the link is not open, or its stack "
               "is not started")
    for url in urls:
        try:
            answer = httpx.get(f"{url}/api/v1/settings/export", timeout=PEER_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            continue
        if answer.status_code != 200:
            trouble = f"the other laptop refused the read (HTTP {answer.status_code})"
            continue
        try:
            body = answer.json()
        except ValueError:
            trouble = "the other laptop answered with something that is not settings"
            continue
        if not isinstance(body, dict) or "settings" not in body:
            trouble = "the other laptop answered with something that is not settings"
            continue
        return body, ""
    return None, trouble


def differences(theirs: Mapping[str, Any], ours: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What a pull would change here, one row per setting, in a shape a screen can render.

    `state` is `new` for a setting this machine has never had and `different` for one it has
    with another value. Settings that already agree are left out — a preview whose long list
    is mostly "unchanged" hides the two rows somebody needs to look at — and counted instead.

    Only what the *other* machine has: a setting this machine has and the other does not is
    not a difference a pull would resolve, because a pull never removes anything.
    """
    here = dict(ours)
    changes: list[dict[str, Any]] = []
    for key, value in sorted(dict(theirs).items()):
        if key not in here:
            changes.append({"key": key, "ours": None, "theirs": value, "state": "new"})
        elif here[key] != value:
            changes.append({"key": key, "ours": here[key], "theirs": value, "state": "different"})
    return changes
