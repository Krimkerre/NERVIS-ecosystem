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
