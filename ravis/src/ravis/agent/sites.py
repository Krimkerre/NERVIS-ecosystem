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
protocol}`, decisions `allow_site` and `keep_blocked` — asked of the owner through Clarvis: one open
at a time, each host once per task. A site ask never holds Codex up (the command has already
failed), so it doesn't make the task wait, the unanswered-request policy ignores it, and it stays
open after the turn ends; it is resolved when the owner decides or the task ends. Only the host is
stored, on the request's row.

**Allowing a site applies live** (verified without a model on Codex 0.154.0): `SiteAllowlist.add`
sends `config/batchWrite` over RAVIS's one Codex connection, upserting `{host: "allow"}` into the
profile's `network.domains` with `reloadUserConfig: true`. Codex writes it to RAVIS's own Codex
home's `config.toml` (never `~/.codex`), with no restart. **A loaded thread doesn't see it**: the
turn already running stayed blocked (run `cal_ed672bf12c6f`, Codex 0.154.0), and so did that
thread's next turn (`cal_85aa0ece0f52`). The owner decided (14 September 2026) that a task may carry
on another way as long as its progress is kept. Calibration found how (run `cal_5a1d6ecc33b4`):
the thread reopened — unsubscribed until Codex unloads it (about 60 s), then `thread/resume` —
reaches the site. Whatever carries a task on after an allow (C2b) must reopen it that way.
Anything but `status: "ok"` — `okOverridden` included, or an error — is 409 `SITE_NOT_ADDED`, and
the site stays blocked. A site is added once however often it's allowed, and only an exact plain
host name: never a wildcard, an IP address, `localhost` or a local name.

**The default sites are written the same way** (Cal-3): each time Codex's process becomes ready,
the Codex service calls `allow_defaults`, one upsert of every `DEFAULT_ALLOWED_SITES` entry. An
upsert merges, so sites the owner added earlier stay in `config.toml`. The launch flags never carry
a site list, because a `-c` flag outranks the file and made every write `okOverridden`.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ravis.agent import refusals
from ravis.agent.calibration_dependent import DEFAULT_ALLOWED_SITES, network_domains_key
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

Request = Callable[..., Awaitable[Any]]


def plain_site(value: object) -> str | None:
    """`value` as a site the owner may allow — a plain public host name, lower-cased — or None."""
    if not isinstance(value, str):
        return None
    host = value.strip().lower().rstrip(".")
    if not HOST.match(host) or host.endswith(LOCAL_SUFFIXES):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


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

    async def add(self, host: str) -> None:
        """Allow one exact host for every task, now; `SITE_NOT_ADDED` when Codex didn't take it."""
        site = plain_site(host)
        if site is None:
            raise refusals.site_not_added(host, "not_a_plain_hostname")
        if site in self._added:
            return
        refused = await self._upsert({site: "allow"})
        if refused is not None:
            raise refusals.site_not_added(site, refused)
        self._added.add(site)

    async def allow_defaults(self) -> str | None:
        """Write every default site into Codex's list: None once Codex took them, else why not.

        The reason is the one word `add` refuses with — `overridden`, `not_written`,
        `codex_did_not_answer` or `no_file_rules_profile`.
        """
        return await self._upsert({site: "allow" for site in DEFAULT_ALLOWED_SITES})

    async def _upsert(self, sites: dict[str, str]) -> str | None:
        """One `config/batchWrite` upsert of `sites`, reloaded: None when Codex answered `ok`."""
        profile = self._profile_name()
        if profile is None:
            return "no_file_rules_profile"
        edit = {"keyPath": network_domains_key(profile), "mergeStrategy": "upsert", "value": sites}
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
