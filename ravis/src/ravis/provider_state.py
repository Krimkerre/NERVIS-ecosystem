"""Which providers an operator has turned off (M10).

Separate from `credentials.py` on purpose, and in a separate file on disk. The
credential file is mode `0600` and holds nothing but secrets; whether a provider
is enabled is ordinary configuration that a person may want to read, diff or
check into a dotfiles repository. Mixing the two would either over-protect the
boring half or under-protect the secret half.

**Disabled is not the same as unconfigured.** A provider with no credential
cannot be reached; a provider an operator has switched off *could* be reached
and must not be. The distinction survives into the routing explanation, because
"excluded: provider disabled" and "excluded: no credential" send someone looking
in different places.

**Default enabled.** A provider absent from the file is on. That way the file
records decisions rather than state — an operator who has never touched a toggle
has an empty file, and nothing has to be migrated when a provider is added.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ravis.credentials import config_directory


class ProviderState:
    """Enabled/disabled per provider, persisted across restarts."""

    def __init__(self, path: Path | None = None, environment: dict[str, str] | None = None) -> None:
        self.path = path or (config_directory(environment) / "providers.json")

    def disabled(self) -> set[str]:
        """Providers explicitly switched off.

        An unreadable or malformed file yields an empty set — everything
        enabled. That is the *permissive* direction, which is the wrong way for
        a security control and the right way for this: an operator whose file
        got corrupted should find their providers working, not find their
        gateway silently serving nothing.
        """
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return set()
        if not isinstance(payload, dict):
            return set()
        return {
            str(name)
            for name, record in payload.items()
            if isinstance(record, dict) and record.get("enabled") is False
        }

    def is_enabled(self, name: str) -> bool:
        return name not in self.disabled()

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Record a decision. Returns the state now in force.

        Both directions are written rather than deleting the entry when
        enabling, so the file shows what has been considered. An operator
        reading it can tell "someone turned this back on" from "nobody has ever
        touched this", which is the difference between a decision and a default.
        """
        payload = self._read()
        payload[name] = {"enabled": enabled}
        self._write(payload)
        return enabled

    def _read(self) -> dict[str, dict[str, bool]]:
        try:
            with self.path.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {
            str(k): {"enabled": bool(v.get("enabled", True))}
            for k, v in loaded.items()
            if isinstance(v, dict)
        }

    def _write(self, payload: dict[str, dict[str, bool]]) -> None:
        """Atomic, like the credential file — but not `0600`.

        Nothing here is secret, and a private file would be a small lie about
        what it contains.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.path)
