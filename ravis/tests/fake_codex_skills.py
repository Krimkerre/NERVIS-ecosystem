"""The skills half of FakeCodexAppServer: Codex's three skills methods, on test folders. Not Codex.

Measured with a throwaway Codex 0.154.0 app-server on 14 September 2026 (a scratch `CODEX_HOME`, no
sign-in, no turns), and acted out here in the stable v2 schema's shapes:

- **`skills/list {cwds, forceReload}`** answers `data[] {cwd, errors, skills[]}`, one entry per
  folder asked about. Its skills are every `SKILL.md` under the user folder (`user`), under
  `$CODEX_HOME/skills/.system` (`system`), under each extra root (`user` as well — so scope alone
  can't tell the NERVIS folder from the owner's own), and under the scenario's project folder
  (`repo`). Each is `enabled` unless a write switched it off.
- **`skills/extraRoots/set {extraRoots}`** answers `{}`. The roots last as long as this process
  only: whether Codex keeps them across a restart is unmeasured, so the fake assumes not.
- **`skills/config/write {path | name, enabled}`** answers `{effectiveEnabled}`, and keeps the
  switch in a file in the Codex home, as Codex keeps it in `config.toml`, so it outlives a restart.

**What a test controls**, in the scenario: `skills_user_root`, the folder standing in for
`~/.agents/skills` (the fake never reads the real one, and has no user skills without it);
`skills_repo_root`, a project's own skills; `skills_write_ignored`, when every write answers the
opposite of what it asked and switches nothing; and `refused_methods` or `silent_methods`, as for
any method. **What it reads**: `skills_roots` for each extra-roots set, `skills_listed` for each
list, and `skills_written {path, enabled}` for each write, in the log.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

#: Where the switches outlive the process: the fake's `config.toml`.
CONFIG_FILE = "fake-skills-config.json"


def handlers(api: Any) -> dict[str, Any]:
    return {
        "skills/list": functools.partial(_list, api),
        "skills/extraRoots/set": functools.partial(_extra_roots, api),
        "skills/config/write": functools.partial(_write, api),
    }


def _home() -> Path:
    return Path(os.environ["CODEX_HOME"])


def _switches() -> dict[str, bool]:
    try:
        loaded = json.loads((_home() / CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {path: value for path, value in loaded.items() if isinstance(value, bool)}


def _extra_roots(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    roots = params.get("extraRoots")
    if not isinstance(roots, list) or not all(
        isinstance(root, str) and os.path.isabs(root) for root in roots
    ):
        return "Invalid request: extraRoots must be absolute paths"
    api.state["skill_roots"] = list(roots)
    api.log("skills_roots", roots=roots)
    return {}


def _frontmatter(text: str) -> dict[str, str]:
    """A SKILL.md's `key: value` lines between its two `---` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    found: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, colon, value = line.partition(":")
        if colon:
            found[key.strip()] = value.strip()
    return found


def _skills_in(root: Path, scope: str, switches: dict[str, bool]) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    skills = []
    for file in sorted(root.rglob("SKILL.md")):
        facts = _frontmatter(file.read_text(encoding="utf-8"))
        path = str(file)
        skills.append({
            "name": facts.get("name", file.parent.name),
            "description": facts.get("description", ""),
            "shortDescription": facts.get("short-description"),
            "path": path, "scope": scope, "enabled": switches.get(path, True),
            "interface": None, "dependencies": None, "pluginId": None,
        })
    return skills


def _list(api: Any, params: dict[str, Any]) -> dict[str, Any]:
    scenario, switches = api.scenario, _switches()
    roots: list[tuple[Path, str]] = []
    if scenario.get("skills_user_root"):
        roots.append((Path(scenario["skills_user_root"]), "user"))
    roots.append((_home() / "skills" / ".system", "system"))
    roots += [(Path(root), "user") for root in api.state.get("skill_roots", [])]
    if scenario.get("skills_repo_root"):
        roots.append((Path(scenario["skills_repo_root"]), "repo"))
    skills = [skill for root, scope in roots for skill in _skills_in(root, scope, switches)]
    api.log("skills_listed", params=params)
    cwds = params.get("cwds") or [os.getcwd()]
    return {"data": [{"cwd": cwd, "errors": [], "skills": skills} for cwd in cwds]}


def _write(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    path, enabled = params.get("path"), params.get("enabled")
    if not isinstance(path, str) or not isinstance(enabled, bool):
        return "Invalid request: skills/config/write needs a path and enabled"
    api.log("skills_written", path=path, enabled=enabled)
    if api.scenario.get("skills_write_ignored"):
        return {"effectiveEnabled": not enabled}
    switches = _switches()
    switches[path] = enabled
    _home().mkdir(parents=True, exist_ok=True)
    (_home() / CONFIG_FILE).write_text(json.dumps(switches), encoding="utf-8")
    return {"effectiveEnabled": enabled}
