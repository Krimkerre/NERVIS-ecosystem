"""M18 — settings export and import, without secrets.

**An allowlist, the same shape as every other boundary in this codebase.**
`inspector.py`'s `DECISION_FIELDS`, `bridges.py`'s `CONFIG_FIELDS`, `logs.py`'s
`FILES` — none of them ask "does this look safe" of an unknown field; each
names exactly what may cross the boundary and refuses everything else by
default. This is that pattern applied to the `setting` table: naming what may
leave is safer than naming what must not, because a setting added later and
forgotten here stays *excluded*, which is the failure mode that costs nothing.
A denylist gets the same forgetting wrong in the dangerous direction.

**Why the `setting` table needs an allowlist at all, when nothing in it is a
credential.** Every credential in this codebase already lives somewhere else —
`NERVIS_FISH_AUDIO_KEY`, the launcher's `.run/*.token` files, the enrollment
secret NERVIS writes beside its own database — never in this key/value store.
So "without secrets" is not the hard part; the hard part is the entries that
are not secret but are not *portable* either:

- `supervision.adapter.<service>` is a literal filesystem path to an
  executable on **this** machine. Importing it on another machine points
  supervision at a path that may not exist, or may exist and be something
  else — the opposite of §12's rule that ownership is configured deliberately,
  never inferred.
- `background.session` is an id this installation minted for itself. Carrying
  it to another machine does not identify anything there.
- `voice.requests.<date>` is a per-day rate-limit counter, one key per day
  forever. It describes what already happened, not a preference, and
  re-importing yesterday's count into a different day is meaningless.
- `chat.memory_excluded` names conversation ids local to this browser's
  history (M20). An id that matches nothing on the destination is inert, but
  it is not a preference either — it is state about conversations that do not
  travel with it.

Everything actually left over — chat presets and parameters, recall,
background's own cadence, the inspector's content switch, the supervision
master switch (harmless with nothing configured to own), voice preferences,
the display name, the poll interval — is a preference a person set and would
reasonably want back after a reinstall.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nervis.errors import InvalidConfigurationError
from nervis.storage import Database

FORMAT = "nervis.settings"
FORMAT_VERSION = 1

# The whole allowlist. Exact keys rather than prefixes: `voice.requests.` is a
# prefix that would otherwise have to be excluded by a second rule, and an
# exact set means a new key is exportable only once somebody has looked at it
# and added it here — which is the point.
EXPORTABLE: frozenset[str] = frozenset({
    "chat.brief", "chat.memory", "chat.memory_skips_current", "chat.mode",
    "chat.params", "chat.preset", "chat.presets", "chat.system",
    "background.enabled", "background.interval_minutes", "background.daily_runs",
    "recall.enabled",
    "inspector.show_content",
    "supervision.enabled",
    "voice.enabled", "voice.selected_profile", "voice.announce_status",
    "voice.trim_long_replies", "voice.fallback", "voice.daily_cap",
    "voice.daily_cap_enabled",
    "user.display_name",
    "ui.remember_tab", "ui.editor_keepalive",
    "refresh_seconds",
})


def _decoded(value: str) -> Any:
    """A stored value, as the type it was written as.

    Matches `api/routes.py`'s own `_decoded` exactly rather than importing it:
    an `api/*` module reads from this one, and the reverse import would be the
    layering backwards for a two-line function.
    """
    try:
        return json.loads(value)
    except ValueError:
        return value


def _encoded(value: Any) -> str:
    """The inverse of `_decoded`, and the reason this is not `json.dumps`.

    **Strings are stored bare in this table**, which is what every reader of it
    expects: `read_setting` hands back the column verbatim and `_profile_for`
    compares it to a profile id. `json.dumps` on a string adds quote characters
    that are part of the value from then on, so a restored backup left
    `voice.selected_profile` holding `"vp_1044b39e85"` — quotes included —
    matching no profile at all, and another restore would have nested them
    again.

    It hid because the settings that looked fine were immune: `json.dumps(True)`
    is `true` and `json.dumps(200)` is `200`, which is already exactly how those
    are stored. Only string-valued settings could carry the damage, and there
    are four of them on the exportable list.

    Everything that is genuinely structured — `chat.presets` is a list — still
    goes through `json.dumps`, because that *is* how those are stored.
    """
    return value if isinstance(value, str) else json.dumps(value)


def export_settings(database: Database) -> dict[str, Any]:
    """Every exportable setting, as a file a person can save and reload.

    Reads every row rather than querying by key, so the allowlist is applied
    once in Python and stays the single place it is enforced — a `WHERE key
    IN (...)` would say the same thing twice and could drift from this set.
    """
    rows = database.connection.execute("SELECT key, value FROM setting")
    kept = {
        row["key"]: _decoded(row["value"])
        for row in rows if row["key"] in EXPORTABLE
    }
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "settings": kept,
    }


@dataclass
class ImportOutcome:
    applied: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"applied": self.applied, "skipped": self.skipped}


def import_settings(database: Database, payload: Any) -> ImportOutcome:
    """Apply whatever in `payload` the allowlist accepts, and report the rest.

    **The allowlist gates both directions, not just export.** A hand-edited or
    forged import file could name any key at all; accepting only what
    `export_settings` would itself have produced is what stops an import from
    being a wider door than the export ever was.

    Refuses the whole file only when it is not recognisably this format —
    §11.5's distinction between a malformed request and one that is merely
    incomplete. A recognisable file with foreign keys in it is not malformed;
    those keys are skipped and named, never silently dropped.
    """
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise InvalidConfigurationError(
            f"not a {FORMAT} file — expected a 'format' field naming it"
        )
    version = payload.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise InvalidConfigurationError(
            f"exported by a version of NERVIS this build does not understand "
            f"(file version {version!r}, this build reads up to {FORMAT_VERSION})"
        )
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise InvalidConfigurationError("'settings' must be an object")

    outcome = ImportOutcome()
    accepted: dict[str, Any] = {}
    for key, value in settings.items():
        if key in EXPORTABLE:
            accepted[key] = value
        else:
            outcome.skipped.append({
                "key": str(key),
                "reason": "not on the exportable list — either a secret, or "
                "state specific to the machine it came from",
            })

    # One transaction for everything accepted, so a settings screen never shows
    # half an import applied and half not.
    with database.connection as connection:
        for key, value in accepted.items():
            connection.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, _encoded(value)),
            )
    outcome.applied = sorted(accepted)
    return outcome


__all__ = [
    "EXPORTABLE", "FORMAT", "FORMAT_VERSION", "ImportOutcome",
    "export_settings", "import_settings",
]
