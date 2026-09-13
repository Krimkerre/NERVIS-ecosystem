"""Cal-3: RAVIS gives Codex its default sites once the process is ready, never in its launch flags.

Calibration run `cal_330b7525d115` found every site write answered `okOverridden`: the launch
flags carried the site list, and a `-c` flag outranks Codex's user configuration. Everything here
runs against `fake_codex_app_server.py`, whose relay half answers a site write `okOverridden`
exactly when a launch flag sets that key, as Codex 0.154.0 does. The rules held:

- **no launch carries a site list**, even when the stored profile still lists sites, and every
  profile calibration checks or pins has them taken out;
- **each time the process becomes ready** — its first start and after a restart — RAVIS upserts
  every default site with a reload, and Codex takes it;
- **a write Codex didn't take keeps Codex not ready**: `GET /api/v1/codex` says why in plain
  words, a new task is refused 409 `CODEX_NOT_READY`, the log says so, and the next start writes
  them again.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.agent_rig import project, ready_rig, refused, serving
from tests.codex_rig import ALLOWANCE, SIGNED_IN, codex_rig, confirmed_record, eventually, reaches

from ravis.agent.calibration_dependent import (
    DEFAULT_ALLOWED_SITES,
    network_profile_flags,
    without_network_domains,
)
from ravis.codex import service as service_module
from ravis.codex.calibration.plan import CANDIDATE_PROFILE, checked_profile
from ravis.codex.pin import file_rules_profile

SITES_KEY = "permissions.clarvis_run.network.domains"
DEFAULTS_WRITE = {
    "edits": [{"keyPath": SITES_KEY, "mergeStrategy": "upsert",
               "value": {site: "allow" for site in DEFAULT_ALLOWED_SITES}}],
    "reloadUserConfig": True,
}
#: A profile as it was pinned before Cal-3: its network section still lists a site.
LISTING = ('permissions.clarvis_run={extends=":workspace", network={enabled=true, mode="limited", '
           'domains={"pypi.org"="allow", "*.crates.io"="allow"}}, '
           'filesystem={"{reproof_decoys}"="deny", "/x/domains"="deny"}}')
OLD_PROFILE = {"name": "clarvis_run", "flags": ["-c", LISTING]}


def writes(rig: Any) -> list[tuple[dict[str, Any], str]]:
    return [(record["params"], record["status"]) for record in rig.server.records("config_written")]


def test_a_site_list_is_taken_out_of_every_profile_before_launch() -> None:
    assert network_profile_flags("clarvis_run") == (
        "-c", 'permissions.clarvis_run.network={enabled=true, mode="limited"}')
    launched = {
        LISTING: 'permissions.clarvis_run={extends=":workspace", network={enabled=true, '
                 'mode="limited"}, filesystem={"{reproof_decoys}"="deny", "/x/domains"="deny"}}',
        'permissions.clarvis_run.network={domains={"a.org"="allow"}, enabled=true}':
            "permissions.clarvis_run.network={enabled=true}",
        "permissions.clarvis_run.network={domains={}}": "permissions.clarvis_run.network={}",
    }
    for given, expected in launched.items():
        assert without_network_domains(["-c", given], "clarvis_run") == ["-c", expected], given
    own_flag = ["-c", 'permissions.clarvis_run.network.domains={"a.org"="allow"}',
                "-c", "permissions.clarvis_run.x=1"]
    assert without_network_domains(own_flag, "clarvis_run") == ["-c", "permissions.clarvis_run.x=1"]
    # Loaded from the pin, checked by calibration (so pinned without), and the candidate itself.
    # (A folder named `domains` is left alone: only a site table is taken out.)
    stored = file_rules_profile({"file_rules_profile": OLD_PROFILE}, {})
    assert stored is not None and "domains=" not in " ".join(stored.flags)
    assert '"/x/domains"="deny"' in " ".join(stored.flags)
    assert "domains=" not in " ".join(checked_profile(OLD_PROFILE)["flags"])
    assert "domains=" not in " ".join(CANDIDATE_PROFILE["flags"])


def test_codex_gets_the_default_sites_at_every_start_and_never_in_its_launch_flags(
    tmp_path: Path,
) -> None:
    confirmed_record()
    rig = codex_rig(tmp_path, proven=True, profile=OLD_PROFILE,
                    scenario={"account": SIGNED_IN, "rate_limits": ALLOWANCE})

    with TestClient(rig.app) as client:
        reaches(client, "signed_in")
        assert writes(rig) == [(DEFAULTS_WRITE, "ok")]
        rig.server.send("crash", status=3)
        eventually(lambda: len(rig.server.records("config_written")) == 2,
                   what="the default sites written again after the restart")
        reaches(client, "signed_in")

    assert writes(rig) == [(DEFAULTS_WRITE, "ok")] * 2
    starts = rig.supervised_starts()
    assert len(starts) == 2
    for started in starts:
        flags = " ".join(started["argv"])
        assert "domains=" not in flags
        assert 'permissions.clarvis_run.network={enabled=true, mode="limited"}' in flags


def test_sites_codex_didnt_take_keep_it_not_ready_until_a_start_that_takes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Run 5's bug put back for the first start only: a site list among the launch flags again.
    original = service_module.network_profile_flags
    listing = {"at_launch": True}

    def launch_network(profile: str) -> tuple[str, str]:
        if not listing["at_launch"]:
            return original(profile)
        return ("-c", f'permissions.{profile}.network={{enabled=true, mode="limited", '
                      'domains={"pypi.org"="allow"}}')

    monkeypatch.setattr(service_module, "network_profile_flags", launch_network)
    caplog.set_level(logging.ERROR, logger="ravis")
    rig = ready_rig(tmp_path)
    root, git_dir = project(rig)

    with serving(rig) as relay:
        def not_ready() -> dict[str, Any] | None:
            body = relay.call("GET", "/api/v1/codex", caller="client.nervis").json()
            overridden = body["state"] == "runtime_down" and "(overridden)" in body["reason"]
            return body if overridden else None

        body = eventually(not_ready, 15, "Codex not ready, saying the sites were overridden")
        assert "default sites" in body["reason"]
        refused(relay.create(root, git_dir), 409, "CODEX_NOT_READY", state="runtime_down",
                reason=body["reason"])
        assert not rig.server.received("thread/start")

        listing["at_launch"] = False
        rig.server.send("crash", status=3)
        eventually(lambda: [status for _, status in writes(rig)] == ["okOverridden", "ok"],
                   15, "the default sites written again at the next start")
        assert relay.ready()["state"] == "signed_in"
        relay.started(root, git_dir)

    assert any("didn't take RAVIS's default sites (overridden)" in record.getMessage()
               for record in caplog.records)
