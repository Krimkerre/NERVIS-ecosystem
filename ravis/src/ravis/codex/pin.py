"""The record of tested Codex builds, `tested_runtimes.json`, and what R2 reads from it.

The pin is committed beside this module and shipped inside the package (design §4.3). R1 reads its
`tested` entries to decide a build's verdict (`runtime.py`). This increment adds three things the
design's format 3 names, and one seam it needs:

- **`used_methods`**: every request RAVIS sends, and every notification and request it reads. A new
  build missing one of them fails acceptance check 5. **A test holds the requests to the code**
  (R6, `test_codex_acceptance.py`): every call in RAVIS's service that names a Codex method must
  name one listed here, because an unlisted one goes unchecked — `config/read`,
  `config/batchWrite` and `permissionProfile/list` were sent unlisted until R6. A request the
  pinned build's stable bundle lacks can't be listed, since check 5 looks in both bundles; the
  surface below holds those (`thread/backgroundTerminals/*`).
- **`strict_rules_surface`**: the experimental definitions and methods the file rules depend on.
  If any of them changed in a new build, check 7a says so — which matters because owner decision
  D2 makes those rules the gate for every task. **A method can be in both lists:**
  `permissionProfile/list` is sent (check 7b asks it whether the `clarvis_run` profile loaded), so
  check 5 needs it, and proving the file rules depends on it, so 7a watches it too.
- **`definitions`** on a tested entry: the file of per-definition hashes of that build's
  experimental schema (`schema_report.py`), which a new build's report is compared against.
- **`file_rules_profile`**: the `clarvis_run` permission profile's `-c` flags, **written at
  calibration**, because the TOML syntax is fixed there from Codex's own validation errors
  (design §4.9). Until then it is `null`: the Codex process starts without the profile, and the
  file-rules re-test refuses to start, since there is nothing it could prove. The flags may name
  four folders by placeholder — `{user_home}`, `{ravis_config}`, `{codex_home}` and
  `{reproof_decoys}` — which RAVIS fills in for this Mac; any other brace is left alone, because
  TOML inline tables are written with braces too. **A site list in the profile is taken out
  before launch** (Cal-3): `domains` in a `-c` flag outranks the sites RAVIS writes once Codex
  runs, and made every write `okOverridden` (`agent/calibration_dependent.py`,
  `without_network_domains`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from ravis.agent.calibration_dependent import without_network_domains

logger = logging.getLogger("ravis")

#: The pin, committed beside this module and shipped inside the package.
TESTED_RUNTIMES: Traversable = resources.files("ravis.codex") / "tested_runtimes.json"
#: The pin's format, as the design numbers it (`design/codex-engine/design.md` §4.3).
PIN_FORMAT = 3
#: The kinds of message the used-methods lists name.
MESSAGE_KINDS = ("client_requests", "server_notifications", "server_requests")
#: The folders a profile's flags may name by placeholder.
PROFILE_PLACEHOLDERS = ("{user_home}", "{ravis_config}", "{codex_home}", "{reproof_decoys}")


class PinUnreadableError(Exception):
    """The committed record can't be read, or isn't the format this RAVIS knows."""


@dataclass(frozen=True)
class UsedSurface:
    """The protocol RAVIS depends on: its used methods, and the file rules' surface."""

    methods: dict[str, tuple[str, ...]]
    surface_definitions: tuple[str, ...]
    surface_methods: tuple[str, ...]


@dataclass(frozen=True)
class FileRulesProfile:
    """The calibrated `clarvis_run` profile: its name, and its flags with this Mac's folders in."""

    name: str
    flags: tuple[str, ...]


def read_pin(pin: Traversable = TESTED_RUNTIMES) -> dict[str, Any]:
    """The whole record, checked to be format 3 with a `tested` list."""
    try:
        document = json.loads(pin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        raise PinUnreadableError(
            f"the record of tested Codex builds can't be read: {failure}"
        ) from None
    tested = document.get("tested") if isinstance(document, dict) else None
    if not isinstance(tested, list) or document.get("format") != PIN_FORMAT:
        raise PinUnreadableError(
            f"the record of tested Codex builds is not format {PIN_FORMAT}"
        )
    return document  # type: ignore[no-any-return]


def used_surface(document: Mapping[str, Any]) -> UsedSurface:
    """The used methods and the strict-rules surface; empty lists when the record has none."""
    used = _mapping(document.get("used_methods"))
    surface = _mapping(document.get("strict_rules_surface"))
    return UsedSurface(
        methods={kind: _names(used.get(kind)) for kind in MESSAGE_KINDS},
        surface_definitions=_names(surface.get("experimental_definitions")),
        surface_methods=_names(surface.get("experimental_methods")),
    )


def file_rules_profile(
    document: Mapping[str, Any], folders: Mapping[str, Path]
) -> FileRulesProfile | None:
    """The calibrated profile, this Mac's folders filled in; None until calibration writes it."""
    raw = document.get("file_rules_profile")
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        return None
    flags = raw.get("flags")
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        logger.warning("codex: the pinned file-rules profile has no usable flags; ignoring it")
        return None
    name = raw["name"]
    # The name goes inside a TOML string below, so it must be a plain identifier.
    plain = name.replace("_", "").replace("-", "")
    if not (0 < len(name) <= 64 and name.isascii() and plain.isalnum()):
        logger.warning("codex: the pinned file-rules profile's name isn't a plain identifier")
        return None
    # A stored profile from before Cal-3 may still list sites; they never reach the launch flags.
    filled = tuple(_filled(flag, folders) for flag in without_network_domains(flags, name))
    # **`default_permissions` names the profile** (found live, 13 September 2026). Codex 0.154.0
    # refuses to start when `[permissions]` defines a profile and nothing chooses one: "config
    # defines `[permissions]` profiles but does not set `default_permissions`". RAVIS started
    # Codex with calibration's candidate profile, Codex exited at every start, and the supervisor
    # kept restarting it. The profile's own flags may only configure `permissions.<name>`
    # (`calibration/plan.py`), so the choice is added here, once, for every profile RAVIS launches
    # with — calibration's and the pinned one alike.
    chosen = ("-c", f'default_permissions="{name}"')
    return FileRulesProfile(name=name, flags=(*filled, *chosen))


def _filled(flag: str, folders: Mapping[str, Path]) -> str:
    for placeholder in PROFILE_PLACEHOLDERS:
        folder = folders.get(placeholder.strip("{}"))
        if folder is not None:
            flag = flag.replace(placeholder, str(folder))
    return flag


def definitions_file(entry: Mapping[str, Any], pin: Traversable) -> dict[str, Any] | None:
    """The per-definition hashes a tested entry names; None when it names none or they won't read.

    The path is relative to the pin's own folder: the package for the shipped pin, the test's
    folder for a pin a test wrote.
    """
    relative = entry.get("definitions")
    if not isinstance(relative, str):
        return None
    base: Traversable = pin.parent if isinstance(pin, Path) else resources.files("ravis.codex")
    try:
        loaded = json.loads((base / relative).read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        logger.warning("codex: the definition hashes %s can't be read: %s", relative, failure)
        return None
    return loaded if isinstance(loaded, dict) else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _names(value: object) -> tuple[str, ...]:
    return tuple(name for name in value if isinstance(name, str)) if isinstance(value, list) else ()
