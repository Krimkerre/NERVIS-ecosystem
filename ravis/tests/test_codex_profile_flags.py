"""The file-rules profile's launch flags choose the profile as Codex's default.

Found live on 13 September 2026: Codex 0.154.0 exits at start with "config defines `[permissions]`
profiles but does not set `default_permissions`" when a profile is defined and none is chosen.
RAVIS launched calibration's candidate profile that way and Codex never came up.
"""

from __future__ import annotations

from pathlib import Path

from ravis.codex.pin import file_rules_profile

FOLDERS = {"user_home": Path("/Users/someone")}
RULES = ["-c", 'permissions.clarvis_run.deny=["{user_home}/.ssh"]']


def test_a_profile_is_launched_as_codexs_default() -> None:
    document = {"file_rules_profile": {"name": "clarvis_run", "flags": RULES}}
    profile = file_rules_profile(document, FOLDERS)

    assert profile is not None
    assert profile.flags[-2:] == ("-c", 'default_permissions="clarvis_run"')
    # The profile's own flags come first, with this Mac's folders filled in.
    assert profile.flags[:2] == ("-c", 'permissions.clarvis_run.deny=["/Users/someone/.ssh"]')


def test_a_name_that_could_break_out_of_the_toml_string_is_refused() -> None:
    for name in ('clarvis_run" \nsandbox_mode="danger-full-access', "", "x" * 65, "clärvis"):
        document = {"file_rules_profile": {"name": name, "flags": RULES}}
        assert file_rules_profile(document, FOLDERS) is None, name


# ── Linux: Codex's own home as one folder (19 September 2026) ─────────────────

PINNED = (
    'permissions.clarvis_run={extends=":workspace", filesystem={":project_roots"={"."="write"}, '
    '"{ravis_config}"="deny", "{codex_home}/auth.json"="deny", "{codex_home}/sessions"="deny", '
    '"{codex_home}/archived_sessions"="deny", "{user_home}/.ssh"="deny", '
    '"{user_home}/.netrc"="deny", "{reproof_decoys}"="deny"}}'
)
HOME_FOLDERS = {"user_home": Path("/home/someone"), "codex_home": Path("/home/someone/codex"),
                "ravis_config": Path("/home/someone/.config/ravis"), "reproof_decoys": Path("/d")}


def _rules(system: str) -> str:
    document = {"file_rules_profile": {"name": "clarvis_run", "flags": ["-c", PINNED]}}
    profile = file_rules_profile(document, HOME_FOLDERS, system=system)
    assert profile is not None
    return profile.flags[1]


def test_on_linux_codexs_home_is_hidden_as_one_folder() -> None:
    """Codex 0.155.1's bwrap can't hide a single existing file (openai/codex#43929); the owner's
    laptop proved the folder rule starts and still hides the sign-in file."""
    rules = _rules("linux")

    assert '"/home/someone/codex"="deny"' in rules
    for entry in ("auth.json", "sessions", "archived_sessions"):
        assert f"/home/someone/codex/{entry}" not in rules
    # Everything else is untouched, ~/.netrc included.
    for kept in ('"/home/someone/.ssh"="deny"', '"/home/someone/.netrc"="deny"', '"/d"="deny"',
                 '"/home/someone/.config/ravis"="deny"'):
        assert kept in rules


def test_on_macos_the_rules_are_as_pinned() -> None:
    rules = _rules("darwin")
    assert '"/home/someone/codex/auth.json"="deny"' in rules
    assert '"/home/someone/codex"="deny"' not in rules


def test_the_folder_rule_never_goes_missing_when_entries_are_removed() -> None:
    """Whatever order or subset of the entries the pin carries, removing them brings the folder."""
    only_sessions = PINNED.replace('"{codex_home}/auth.json"="deny", ', "")
    document = {"file_rules_profile": {"name": "clarvis_run", "flags": ["-c", only_sessions]}}
    profile = file_rules_profile(document, HOME_FOLDERS, system="linux")
    assert profile is not None
    assert '"/home/someone/codex"="deny"' in profile.flags[1]
    assert "/home/someone/codex/sessions" not in profile.flags[1]
