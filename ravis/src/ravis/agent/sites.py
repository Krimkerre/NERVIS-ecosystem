"""Blocked sites asked of the owner, and the allowlist that lets Codex's commands reach them.

**Why a site ask and not a network approval** (the owner's decision, 13 September 2026). Calibration
proved that on Codex 0.154.0 an approval never opens the network, in `untrusted` and `on-request`
alike (`calibration_dependent.py`, `NETWORK_GRANTS_OFFERED`). Internet access comes from an
approved-sites allowlist instead: Codex runs with its network proxy on (`features.network_proxy`)
and a profile section `network={enabled=true, mode="limited", domains={…}}` listing the sites
commands may reach — `DEFAULT_ALLOWED_SITES` to start with — and the proxy blocks every other host
with one fixed line (`BLOCKED` below).

**The ask.** When a completed command's output carries that line, the task emits `site.blocked
{turn_id, item_id, host, protocol}` and opens a request of kind `site` — payload `{host,
protocol}`, decisions `allow_site` and `keep_blocked` — asked of the owner through Clarvis, each
host once per task. **Asks come grouped** (R5, the owner's decision of 14 September 2026): the asks
one turn opens share a `group_id` and are open together, so Clarvis can show them as one card with
**Allow all**; one group is open at a time, and a later turn's asks wait until every ask of the open
group is decided (`SiteAsks`). A site ask never holds Codex up (the command has already failed), so
it doesn't make the task wait, the unanswered-request policy ignores it, and it stays open after the
turn ends; it is resolved when the owner decides or the task ends. Only the host is stored, on the
request's row.

**Allowing a site applies live** (verified without a model on Codex 0.154.0): `SiteAllowlist.add`
sends `config/batchWrite` over RAVIS's one Codex connection, upserting `{host: "allow"}` into the
profile's `network.domains` with `reloadUserConfig: true`. Codex writes it to RAVIS's own Codex
home's `config.toml` (never `~/.codex`), with no restart. **A loaded thread doesn't see it**: the
turn already running stayed blocked (run `cal_ed672bf12c6f`, Codex 0.154.0), and so did that
thread's next turn (`cal_85aa0ece0f52`). The thread reopened — unsubscribed until Codex unloads it
(about 60 s), then `thread/resume` — reaches the site (run `cal_5a1d6ecc33b4`), and since R5 RAVIS
reopens a task's thread itself (`reopen.py`, and `session.py`'s reopening). Anything but
`status: "ok"` — `okOverridden` included, or an error — is 409 `SITE_NOT_ADDED`, and the site stays
blocked. A site is added once however often it's allowed, and only an exact plain host name: never a
wildcard, an IP address, `localhost` or a local name.

**The default sites are written the same way** (Cal-3): each time Codex's process becomes ready,
the Codex service calls `allow_defaults`, one upsert of every `DEFAULT_ALLOWED_SITES` entry. An
upsert merges, so sites the owner added earlier stay in `config.toml`. The launch flags never carry
a site list, because a `-c` flag outranks the file and made every write `okOverridden`.

**The owner's list, before a task** (R5, the owner's option 1): `GET`, `POST` and `DELETE
/api/v1/codex/sites` (`api/management/codex.py`) read and change the same list. `listed` reads the
profile's sites from Codex's user configuration (`config/read` with its layers, as calibration's K3
reads them); `allow` checks every host by the rule a site ask follows and writes nothing unless all
pass; `remove` never takes out a default and writes the rest back with one `replace`. **Every write
waits for the one before it** — these, a site ask's and the defaults' — so a removal's read-then-
replace can never drop a site allowed in between. A change reaches the threads Codex loads
afterwards: new ones, and reopened ones.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ravis.agent import refusals
from ravis.agent.calibration_dependent import DEFAULT_ALLOWED_SITES, network_domains_key
from ravis.agent.tokens import new_id
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError

#: Codex's proxy's fixed line for a host that isn't on the list.
BLOCKED = re.compile(
    r'Network access to "([^"\s]{1,260})" was blocked: '
    r"domain is not on the allowlist for the current sandbox mode\."
)
#: A plain host name: dotted labels ending in a name, lower-case; no port, path, scheme or wildcard.
HOST = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)
#: Names that only ever mean this Mac or its local network.
LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa", ".lan")
DECISIONS = ("allow_site", "keep_blocked")
WRITE_SECONDS = 10.0
#: At most this many hosts in one `POST /api/v1/codex/sites`.
MOST_HOSTS = 20
#: Why a host can't be allowed, in the words `SITES_REFUSED` names (`conventions.json`).
NOT_A_HOST_NAME = "not_a_host_name"
WILDCARD = "wildcard"
IP_ADDRESS = "ip_address"
LOCAL_NAME = "local_name"
#: What the sites routes answer while Codex can't say which sites it allows (503, retryable).
UNREADABLE = "Codex isn't running, so the sites it allows can't be read; try again in a moment."

Request = Callable[..., Awaitable[Any]]
#: `SitesView` (`codex-admin.json`): `{defaults: [...], added: [...]}`.
SitesView = dict[str, list[str]]


def site_refusal(value: object) -> str | None:
    """Why `value` can't be allowed as a site, in one word; None for a plain public host name."""
    if not isinstance(value, str):
        return NOT_A_HOST_NAME
    host = _normalised(value)
    if "*" in host:
        return WILDCARD
    if _ip_address(host):
        return IP_ADDRESS
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return LOCAL_NAME
    return None if HOST.match(host) else NOT_A_HOST_NAME


def plain_site(value: object) -> str | None:
    """`value` as a site the owner may allow — a plain public host name, lower-cased — or None."""
    if not isinstance(value, str) or site_refusal(value) is not None:
        return None
    return _normalised(value)


def checked_hosts(values: list[object], action: str) -> list[str]:
    """Hosts as sites, each once and in order — or `SITES_REFUSED` naming every refused one.

    `action` is what a refusal means didn't happen to any of them: `added` or `removed`.
    """
    refused = [{"host": str(value), "reason": reason}
               for value in values if (reason := site_refusal(value)) is not None]
    if refused:
        raise refusals.sites_refused(refused, action)
    return list(dict.fromkeys(_normalised(str(value)) for value in values))


def _normalised(value: str) -> str:
    return value.strip().lower().rstrip(".")


def _ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def blocked_hosts(output: object) -> list[str]:
    """The sites the proxy's fixed line names in a command's output: each once, in order."""
    if not isinstance(output, str):
        return []
    sites = (plain_site(found) for found in BLOCKED.findall(output))
    return list(dict.fromkeys(site for site in sites if site is not None))


def protocol_for(host: str, command: object) -> str | None:
    """`http` or `https` when the command names the host with one; otherwise unknown."""
    if not isinstance(command, str):
        return None
    found = re.search(rf"\b(https?)://{re.escape(host)}(?![A-Za-z0-9.-])", command, re.IGNORECASE)
    return found.group(1).lower() if found else None


def site_payload(host: str, protocol: str | None) -> dict[str, str | None]:
    """A site request's payload (`agent-sessions.json` → `request_kinds` → `site`)."""
    return {"host": host, "protocol": protocol}


def user_sites(read: object, profile: str) -> dict[str, Any] | None:
    """The profile's sites in `config/read`'s user layer: {} when it has none, None if unknown."""
    layers = read.get("layers") if isinstance(read, dict) else None
    if not isinstance(layers, list):
        return None
    for layer in layers:
        name = layer.get("name") if isinstance(layer, dict) else None
        if isinstance(name, dict) and name.get("type") == "user":
            node: object = layer.get("config")
            for key in ("permissions", profile, "network", "domains"):
                node = node.get(key) if isinstance(node, dict) else None
            return dict(node) if isinstance(node, dict) else {}
    return {}


def sites_view(sites: Mapping[str, object]) -> SitesView:
    """`SitesView`: RAVIS's defaults, and every other site Codex's list allows, sorted."""
    allowed = (host for host, word in sites.items() if word == "allow")
    return {"defaults": list(DEFAULT_ALLOWED_SITES),
            "added": sorted(host for host in allowed if host not in DEFAULT_ALLOWED_SITES)}


# ── One task's asks, grouped ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SiteAsk:
    """A site Codex's proxy blocked in one of a task's turns: waiting to be asked, or asked."""

    host: str
    protocol: str | None
    turn_id: Any
    item_id: Any
    group_id: str


class SiteAsks:
    """One task's site asks: grouped by the turn that blocked them, opened a group at a time."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._groups: dict[object, str] = {}
        self._hosts: dict[str, list[str]] = {}
        self._waiting: list[SiteAsk] = []

    def blocked(
        self, turn_id: Any, item_id: Any, host: str, protocol: str | None
    ) -> SiteAsk | None:
        """A host the proxy blocked, queued in its turn's group; None for a host asked before."""
        if host in self._seen:
            return None
        self._seen.add(host)
        group = self._groups.get(turn_id)
        if group is None:
            group = self._groups[turn_id] = new_id("sg_")
        self._hosts.setdefault(group, []).append(host)
        ask = SiteAsk(host, protocol, turn_id, item_id, group)
        self._waiting.append(ask)
        return ask

    def openable(self, open_groups: set[str]) -> list[SiteAsk]:
        """The waiting asks that open now: the open group's, or else the oldest waiting group's."""
        if not self._waiting:
            return []
        groups = open_groups or {self._waiting[0].group_id}
        ready = [ask for ask in self._waiting if ask.group_id in groups]
        self._waiting = [ask for ask in self._waiting if ask.group_id not in groups]
        return ready

    def group_of(self, turn_id: Any) -> str | None:
        """The group a turn's asks form, if that turn blocked any site."""
        return self._groups.get(turn_id)

    def hosts(self, group_id: str) -> list[str]:
        """Every host a group asked about, in the order the proxy blocked them."""
        return list(self._hosts.get(group_id, ()))

    def waiting(self, group_id: str) -> bool:
        """Whether an ask of this group is still waiting for another group to be decided."""
        return any(ask.group_id == group_id for ask in self._waiting)


# ── Codex's list ─────────────────────────────────────────────────────────────


class SiteAllowlist:
    """The sites Codex's commands may reach beyond the defaults, added while Codex runs."""

    def __init__(
        self, request: Request, profile_name: Callable[[], str | None],
        *, seconds: float = WRITE_SECONDS,
    ) -> None:
        self._request = request
        self._profile_name = profile_name
        self._seconds = seconds
        self._added: set[str] = set()
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _writing(self) -> asyncio.Lock:
        """The one-write-at-a-time lock, made for the event loop that runs (a test may run two)."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    async def add(self, host: str) -> None:
        """Allow one exact host for every task, now; `SITE_NOT_ADDED` when Codex didn't take it."""
        site = plain_site(host)
        if site is None:
            raise refusals.site_not_added(host, "not_a_plain_hostname")
        if site in self._added:
            return
        async with self._writing():
            refused = await self._write({site: "allow"}, "upsert")
        if refused is not None:
            raise refusals.site_not_added(site, refused)
        self._added.add(site)

    async def allow_defaults(self) -> str | None:
        """Write every default site into Codex's list: None once Codex took them, else why not.

        The reason is the one word `add` refuses with — `overridden`, `not_written`,
        `codex_did_not_answer` or `no_file_rules_profile`.
        """
        async with self._writing():
            return await self._write({site: "allow" for site in DEFAULT_ALLOWED_SITES}, "upsert")

    async def listed(self) -> SitesView:
        """`GET /api/v1/codex/sites`: the defaults and the sites added, as Codex holds them now."""
        return sites_view(await self._read())

    async def allow(self, values: list[object]) -> tuple[list[str], SitesView]:
        """`POST /api/v1/codex/sites`: every host checked first, then one upsert of them all.

        Codex's list is read before the write, so nothing is written unless the answer can be
        given; the hosts as written come back with the view, for the audit.
        """
        hosts = checked_hosts(values, "added")
        async with self._writing():
            sites = await self._read()
            refused = await self._write(dict.fromkeys(hosts, "allow"), "upsert")
        if refused is not None:
            raise refusals.sites_not_added(hosts, refused)
        self._added.update(hosts)
        return hosts, sites_view({**sites, **dict.fromkeys(hosts, "allow")})

    async def remove(self, value: str) -> tuple[str | None, SitesView]:
        """`DELETE /api/v1/codex/sites/{host}`: never a default; the rest written back in one go.

        Answers the host removed — or None when it wasn't on the added list, and nothing was
        written — with the view as Codex now holds it.
        """
        if _normalised(value) in DEFAULT_ALLOWED_SITES:
            raise refusals.site_not_removed(_normalised(value), "default_site")
        [host] = checked_hosts([value], "removed")
        async with self._writing():
            sites = await self._read()
            if sites.get(host) != "allow":
                return None, sites_view(sites)
            rest = {name: word for name, word in sites.items() if name != host}
            refused = await self._write(rest, "replace")
        if refused is not None:
            raise refusals.site_not_removed(host, refused)
        # Forgotten here, so a later ask that allows it again writes it again.
        self._added.discard(host)
        return host, sites_view(rest)

    async def _read(self) -> dict[str, Any]:
        """The profile's sites in Codex's user configuration; 503 when Codex can't say."""
        profile = self._profile_name()
        if profile is None:
            raise refusals.runtime_unavailable(UNREADABLE)
        try:
            read = await self._request("config/read", {"includeLayers": True},
                                       timeout=self._seconds)
        except (CodexRpcError, CodexUnavailableError):
            raise refusals.runtime_unavailable(UNREADABLE) from None
        sites = user_sites(read, profile)
        if sites is None:
            raise refusals.runtime_unavailable(UNREADABLE)
        return sites

    async def _write(self, sites: Mapping[str, Any], strategy: str) -> str | None:
        """One `config/batchWrite` of the profile's sites, reloaded: None when Codex answered `ok`.

        `upsert` merges the sites into Codex's list; `replace` makes them the whole list.
        """
        profile = self._profile_name()
        if profile is None:
            return "no_file_rules_profile"
        edit = {"keyPath": network_domains_key(profile), "mergeStrategy": strategy,
                "value": dict(sites)}
        try:
            result = await self._request("config/batchWrite",
                                         {"edits": [edit], "reloadUserConfig": True},
                                         timeout=self._seconds)
        except (CodexRpcError, CodexUnavailableError):
            return "codex_did_not_answer"
        status = result.get("status") if isinstance(result, dict) else None
        if status == "ok":
            return None
        return "overridden" if status == "okOverridden" else "not_written"
