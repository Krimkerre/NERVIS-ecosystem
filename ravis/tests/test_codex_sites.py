"""The sites Codex's commands may reach, allowed before a task and removed by the owner (R5).

`GET`, `POST` and `DELETE /api/v1/codex/sites` (`codex-admin.json`), run against the fake
app-server: its relay half answers each site write as Codex 0.154.0 does, and its calibration half
keeps the list `config/read` shows. What RAVIS is held to:
- **each caller only what the contract lets it**: Clarvis, NERVIS and admin credentials read; only
  Clarvis adds; only an admin removes;
- **a host RAVIS never allows refuses the whole add**, naming each refused host with why, and
  nothing is written; a body that isn't 1 to 20 host names is 422;
- **one write for all the hosts**, audited with the hosts and nothing else; a write Codex doesn't
  take is 409 `SITE_NOT_ADDED` and isn't audited;
- **a default is never removed**; a removal writes the rest back with one `replace`, one Codex
  doesn't take is 409 `SITE_NOT_REMOVED`, and a host that isn't on the list writes nothing;
- while Codex doesn't say which sites it allows, 503 — never an empty list;
- a site removed and allowed again is written again, and a removal never drops a site allowed while
  it ran.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import fixture, ready_rig, refused, serving

from ravis.agent.calibration_dependent import DEFAULT_ALLOWED_SITES
from ravis.agent.refusals import ADMIN_REFUSED
from ravis.agent.sites import SiteAllowlist, checked_hosts, site_refusal
from ravis.codex.refusals import CodexRefusalError

SITES = "/api/v1/codex/sites"
ONE_SITE = "/api/v1/codex/sites/{host}"
SITES_KEY = "permissions.clarvis_run.network.domains"


def case(method: str, path: str, name: str) -> dict[str, Any]:
    """One of `codex-admin.json`'s examples for a sites route, request and response."""
    route = next(r for r in fixture("codex-admin.json")["routes"]
                 if (r["method"], r["path"]) == (method, path))
    return next(example for example in route["examples"] if example["name"] == name)  # type: ignore[no-any-return]


def message(method: str, path: str, name: str) -> str:
    return case(method, path, name)["response"]["body"]["error"]["message"]  # type: ignore[no-any-return]


def writes(rig: Any) -> list[dict[str, Any]]:
    """Every site write after RAVIS's defaults (taken as Codex became ready): edit and status."""
    records = rig.server.records("config_written")
    assert records and records[0]["params"]["edits"][0]["value"] == dict.fromkeys(
        DEFAULT_ALLOWED_SITES, "allow")
    return [{"edit": record["params"]["edits"][0], "status": record["status"]}
            for record in records[1:]]


def audited(rig: Any, event: str, field: str) -> list[Any]:
    return [data[field] for data in rig.published(event)]


def test_each_caller_reads_adds_or_removes_the_sites_only_as_the_contract_lets_it(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    with serving(rig) as relay:
        relay.ready()
        empty = relay.call("GET", SITES, caller="client.clarvis")
        assert (empty.status_code, empty.json()) == (
            200, {"defaults": list(DEFAULT_ALLOWED_SITES), "added": []})
        allowed = case("POST", SITES, "allowed before a task starts")
        added = relay.call("POST", SITES, caller="client.clarvis",
                           body=allowed["request"]["body"])
        assert (added.status_code, added.json()) == (200, allowed["response"]["body"])
        assert writes(rig) == [{"edit": {
            "keyPath": SITES_KEY, "mergeStrategy": "upsert",
            "value": {"download.pytorch.org": "allow", "huggingface.co": "allow"},
        }, "status": "ok"}]
        assert audited(rig, "ravis.codex.sites_allowed", "hosts") == [
            ["download.pytorch.org", "huggingface.co"]]
        for reader in ("client.nervis", "admin.launcher", "admin.owner_cli"):
            read = relay.call("GET", SITES, caller=reader)
            assert (read.status_code, read.json()) == (200, allowed["response"]["body"]), reader
        for stranger in ("anonymous", "client.other"):
            error = refused(relay.call("GET", SITES, caller=stranger), 403, "FORBIDDEN")
            assert error["message"] == message("GET", SITES, "anonymous"), stranger
        body = {"hosts": ["example.com"]}
        nervis = refused(relay.call("POST", SITES, caller="client.nervis", body=body), 403,
                         "AGENT_CLIENT_NOT_ALLOWED")
        assert nervis["message"] == message("POST", SITES, "NERVIS's relay")
        admin = refused(relay.call("POST", SITES, caller="admin.launcher", body=body), 403,
                        "AGENT_CLIENT_NOT_ALLOWED")
        assert admin["message"] == ADMIN_REFUSED
        refused(relay.call("POST", SITES, caller="anonymous", body=body), 403,
                "AGENT_CLIENT_NOT_ALLOWED")
        client = refused(relay.call("DELETE", f"{SITES}/huggingface.co", caller="client.clarvis"),
                         403, "FORBIDDEN")
        assert client["message"] == message("DELETE", ONE_SITE, "a client credential")
        assert len(writes(rig)) == 1


def test_a_host_ravis_never_allows_refuses_the_whole_add_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    shown = case("POST", SITES, "a host RAVIS never allows: nothing is written")
    with serving(rig) as relay:
        relay.ready()
        response = relay.call("POST", SITES, caller="client.clarvis",
                              body=shown["request"]["body"])
        error = refused(response, 422, "SITES_REFUSED",
                        refused=shown["response"]["body"]["error"]["details"]["refused"])
        assert error["message"] == shown["response"]["body"]["error"]["message"]
        local = relay.call("POST", SITES, caller="client.clarvis",
                           body={"hosts": ["localhost", "printer.local", "pypi.org:443"]})
        refused(local, 422, "SITES_REFUSED", refused=[
            {"host": "localhost", "reason": "local_name"},
            {"host": "printer.local", "reason": "local_name"},
            {"host": "pypi.org:443", "reason": "not_a_host_name"}])
        no_hosts = relay.call("POST", SITES, caller="client.clarvis", body={"hosts": []})
        assert refused(no_hosts, 422, "INVALID_REQUEST_BODY")["message"] == message(
            "POST", SITES, "no hosts")
        for body in ({}, {"hosts": "pypi.org"}, {"hosts": [1]},
                     {"hosts": [f"site-{n}.example.com" for n in range(21)]}):
            refused(relay.call("POST", SITES, caller="client.clarvis", body=body), 422,
                    "INVALID_REQUEST_BODY")
        assert writes(rig) == []
        assert relay.call("GET", SITES, caller="client.clarvis").json()["added"] == []
    assert not rig.published("ravis.codex.sites_allowed")


def test_sites_codex_doesnt_take_stay_blocked_and_arent_audited(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"site_add_status": "okOverridden"})
    with serving(rig) as relay:
        relay.ready()
        response = relay.call("POST", SITES, caller="client.clarvis",
                              body={"hosts": ["huggingface.co"]})
        error = refused(response, 409, "SITE_NOT_ADDED", hosts=["huggingface.co"],
                        reason="overridden")
        assert error["message"] == message("POST", SITES, "Codex didn't add them")
        assert relay.call("GET", SITES, caller="client.clarvis").json()["added"] == []
    assert not rig.published("ravis.codex.sites_allowed")


def test_removing_a_site_writes_the_rest_back_and_never_takes_out_a_default(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path)
    with serving(rig) as relay:
        relay.ready()
        relay.call("POST", SITES, caller="client.clarvis",
                   body={"hosts": ["download.pytorch.org", "huggingface.co"]})
        removed = relay.call("DELETE", f"{SITES}/huggingface.co", caller="admin.launcher")
        assert (removed.status_code, removed.json()) == (
            200, case("DELETE", ONE_SITE, "removed")["response"]["body"])
        rest = dict.fromkeys((*DEFAULT_ALLOWED_SITES, "download.pytorch.org"), "allow")
        assert writes(rig)[-1] == {"edit": {"keyPath": SITES_KEY, "mergeStrategy": "replace",
                                            "value": rest}, "status": "ok"}
        assert audited(rig, "ravis.codex.site_removed", "host") == ["huggingface.co"]
        written = len(writes(rig))
        for default, host in (("pypi.org", "pypi.org"), ("*.crates.io", "*.crates.io"),
                              ("PyPI.org", "pypi.org")):
            error = refused(relay.call("DELETE", f"{SITES}/{default}", caller="admin.launcher"),
                            409, "SITE_NOT_REMOVED", host=host, reason="default_site")
            shown = message("DELETE", ONE_SITE, "a default site is never removed")
            assert error["message"] == shown
        unlisted = relay.call("DELETE", f"{SITES}/example.org", caller="admin.launcher")
        assert (unlisted.status_code, unlisted.json()["added"]) == (200, ["download.pytorch.org"])
        refused(relay.call("DELETE", f"{SITES}/192.168.1.20", caller="admin.launcher"), 422,
                "SITES_REFUSED", refused=[{"host": "192.168.1.20", "reason": "ip_address"}])
        assert len(writes(rig)) == written
        assert relay.call("GET", SITES, caller="client.nervis").json()["added"] == [
            "download.pytorch.org"]
    assert audited(rig, "ravis.codex.site_removed", "host") == ["huggingface.co"]


def test_a_removal_codex_doesnt_take_leaves_the_site_allowed(tmp_path: Path) -> None:
    rig = ready_rig(tmp_path, scenario={"site_replace_status": "okOverridden"})
    with serving(rig) as relay:
        relay.ready()
        relay.call("POST", SITES, caller="client.clarvis", body={"hosts": ["huggingface.co"]})
        error = refused(relay.call("DELETE", f"{SITES}/huggingface.co", caller="admin.launcher"),
                        409, "SITE_NOT_REMOVED", host="huggingface.co", reason="overridden")
        assert error["message"] == message("DELETE", ONE_SITE, "Codex didn't remove it")
        assert relay.call("GET", SITES, caller="client.clarvis").json()["added"] == [
            "huggingface.co"]
    assert not rig.published("ravis.codex.site_removed")


def test_the_sites_arent_read_or_changed_while_codex_doesnt_say_which_it_allows(
    tmp_path: Path,
) -> None:
    rig = ready_rig(tmp_path, scenario={"refused_methods": ["config/read"]})
    with serving(rig) as relay:
        relay.ready()
        read = refused(relay.call("GET", SITES, caller="client.nervis"), 503,
                       "CODEX_RUNTIME_UNAVAILABLE")
        assert read["retryable"] is True
        assert read["message"] == message("GET", SITES, "Codex isn't running")
        added = refused(relay.call("POST", SITES, caller="client.clarvis",
                                   body={"hosts": ["huggingface.co"]}), 503,
                        "CODEX_RUNTIME_UNAVAILABLE")
        assert added["retryable"] is True
        assert added["message"] == message("POST", SITES, "Codex isn't running")
        refused(relay.call("DELETE", f"{SITES}/huggingface.co", caller="admin.launcher"), 503,
                "CODEX_RUNTIME_UNAVAILABLE")
        assert writes(rig) == []


# ── The allowlist on its own ─────────────────────────────────────────────────


class FakeConfiguration:
    """Codex's user configuration as `config/read` and `config/batchWrite` see it, in memory.

    A read takes `pause` seconds to come back, as a real one crossing the pipe takes a moment, so a
    write arriving meanwhile can be put between a removal's read and its replace.
    """

    def __init__(self, pause: float = 0.0) -> None:
        self.sites: dict[str, str] = {}
        self.written: list[tuple[str, dict[str, str]]] = []
        self.pause = pause

    async def request(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        del timeout
        if method == "config/read":
            seen = dict(self.sites)
            await asyncio.sleep(self.pause)
            domains = {"clarvis_run": {"network": {"domains": seen}}}
            return {"layers": [{"name": {"type": "user"}, "config": {"permissions": domains}}]}
        edit = params["edits"][0]
        self.written.append((edit["mergeStrategy"], dict(edit["value"])))
        if edit["mergeStrategy"] == "replace":
            self.sites = dict(edit["value"])
        else:
            self.sites.update(edit["value"])
        return {"status": "ok"}


async def test_a_site_removed_and_allowed_again_is_written_again() -> None:
    codex = FakeConfiguration()
    sites = SiteAllowlist(codex.request, lambda: "clarvis_run")
    await sites.add("huggingface.co")
    await sites.add("huggingface.co")  # added already: no second write
    assert (await sites.remove("huggingface.co"))[0] == "huggingface.co"
    await sites.add("huggingface.co")
    assert [strategy for strategy, _ in codex.written] == ["upsert", "replace", "upsert"]
    assert (await sites.listed())["added"] == ["huggingface.co"]


async def test_a_removal_never_drops_a_site_allowed_while_it_ran() -> None:
    codex = FakeConfiguration(pause=0.1)
    sites = SiteAllowlist(codex.request, lambda: "clarvis_run")
    await sites.allow(["download.pytorch.org", "huggingface.co"])
    removal = asyncio.create_task(sites.remove("download.pytorch.org"))
    await asyncio.sleep(0.02)  # the removal has read the list, and waits for the answer
    await sites.add("example.com")
    await removal
    assert codex.sites == {"huggingface.co": "allow", "example.com": "allow"}


def test_each_refused_host_is_named_with_the_reason_the_contract_gives() -> None:
    values: list[object] = ["*.hf.co", "192.168.1.20", "[::1]", "localhost", "printer.local",
                            "https://example.com/data", "pypi.org:443", "", 7]
    assert [site_refusal(value) for value in values] == [
        "wildcard", "ip_address", "ip_address", "local_name", "local_name", "not_a_host_name",
        "not_a_host_name", "not_a_host_name", "not_a_host_name"]
    assert site_refusal(" PyPI.org. ") is None
    assert checked_hosts(["HuggingFace.co", "huggingface.co."], "added") == ["huggingface.co"]
    with pytest.raises(CodexRefusalError) as refusal:
        checked_hosts(["huggingface.co", "*.hf.co"], "removed")
    assert refusal.value.code == "SITES_REFUSED"
    assert refusal.value.details == {"refused": [{"host": "*.hf.co", "reason": "wildcard"}]}
    assert str(refusal.value).endswith("so nothing was removed.")


def test_the_contracts_sites_examples_list_ravis_own_defaults() -> None:
    views = [example["response"]["body"] for route in fixture("codex-admin.json")["routes"]
             if route["path"] in (SITES, ONE_SITE) for example in route["examples"]
             if "defaults" in example["response"]["body"]]
    assert len(views) == 3
    assert all(view["defaults"] == list(DEFAULT_ALLOWED_SITES) for view in views)
