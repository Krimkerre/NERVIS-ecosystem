"""The one door the skill store reaches the internet through (RAVIS 0.28.0).

**Why a door of its own** (owner decisions, 15 September 2026). NERVIS's Skills page installs
skills from GitHub, from skills.sh's search and from websites that publish an Agent Skills index.
Everything RAVIS fetches for that is text a model may later follow, or a script Codex may run, so
every fetch goes through these rules and nowhere else:

- **`https` only, to the hosts one call allows.** GitHub's four hosts (`GITHUB_HOSTS`) for GitHub
  sources, link lists and skills.sh's results; skills.sh's own host only for its search; and for a
  website the owner added, exactly that website's host. A URL naming a user, a password or another
  port than its host allows is refused before anything is sent.
- **Redirects are followed by hand**, at most `MOST_REDIRECTS`, and only while each one stays on a
  host the same call allows: GitHub answering a renamed repository with a redirect to
  `api.github.com` works, a website sending RAVIS to another site doesn't.
- **Timeouts and size caps.** `CONNECT_SECONDS` to connect, `READ_SECONDS` between bytes, and
  `TOTAL_SECONDS` for the whole answer, so a server dripping bytes can't hold a request open; each
  call names the most bytes it will read, and the answer is refused past that, while streaming.
- **No credentials, ever.** No `Authorization` header is sent, and `trust_env` is off, so neither
  a `~/.netrc` entry for github.com nor a proxy setting in the environment can add one.
- **Rate limits are respected, not retried.** GitHub allows about 60 API calls an hour without a
  token. An answer saying the limit is reached (`x-ratelimit-remaining: 0`, `retry-after`, or a
  429) raises `RateLimitedError` with the time it lifts, and RAVIS sends nothing more to that host
  until then. `api_allowance` keeps what GitHub last said is left, so lazy work can stop early and
  leave the rest of the hour to installs.

Tests never reach the network: `live_transport` is replaced for the whole suite (`conftest.py`), and
the skill store's tests hand `Web` a fake GitHub and fake websites.
"""

from __future__ import annotations

import asyncio
import email.utils
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

import httpx

#: The hosts a GitHub source, a link list or a skills.sh result may be fetched from.
GITHUB_HOSTS = frozenset({
    "github.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com",
})
#: GitHub's API host: the one whose hourly allowance RAVIS keeps track of.
GITHUB_API = "api.github.com"
MOST_REDIRECTS = 5
CONNECT_SECONDS = 5.0
READ_SECONDS = 20.0
TOTAL_SECONDS = 60.0
#: How long RAVIS waits when a host says it is rate-limiting without saying for how long.
UNSAID_WAIT = timedelta(seconds=60)
#: Who is asking. GitHub's API refuses a request without a User-Agent.
USER_AGENT = "RAVIS-skill-store (https://agentskills.io/specification)"
REDIRECTS = frozenset({301, 302, 303, 307, 308})


# ── What can go wrong, in plain words ───────────────────────────────────────


class WebError(Exception):
    """A fetch that didn't give RAVIS an answer it can use; `why` says so in plain words."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


class NotFoundError(WebError):
    """The host answered that there is nothing at that address (or nothing RAVIS may see)."""


class RateLimitedError(WebError):
    """The host is rate-limiting RAVIS until `retry_at`; nothing more is sent to it until then."""

    def __init__(self, host: str, retry_at: datetime) -> None:
        super().__init__(f"{host} is rate-limiting RAVIS until {retry_at.isoformat()}")
        self.host = host
        self.retry_at = retry_at


class UnreachableError(WebError):
    """No usable answer: the network, a timeout, or a status RAVIS doesn't read."""


class RefusedError(WebError):
    """A rule of this module refused the fetch; `reason` is the machine-readable word.

    `not_https`, `host_not_allowed`, `redirect_elsewhere`, `too_many_redirects` or `too_large`.
    """

    def __init__(self, reason: str, why: str) -> None:
        super().__init__(why)
        self.reason = reason


@dataclass(frozen=True)
class Fetched:
    """One answer read whole: where it finally came from, its status and headers, and its body."""

    url: str
    status: int
    headers: httpx.Headers
    body: bytes


def live_transport() -> httpx.AsyncBaseTransport | None:
    """The transport a `Web` built without one uses: httpx's own, to the real internet.

    A function, so the test suite can swap in one that fails any test reaching the network.
    """
    return None


def host_of(url: str) -> str | None:
    """A URL's host as the allow-lists name it: lower case, with a port only when it isn't 443;
    None when it isn't an `https` URL RAVIS could fetch (no host, a user or password in it)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    return host if port in (None, 443) else f"{host}:{port}"


# ── The door ─────────────────────────────────────────────────────────────────


class Web:
    """Every fetch the skill store makes, under this module's rules."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._transport = transport if transport is not None else live_transport()
        self._now = now
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        #: Hosts RAVIS sends nothing to until the time each says its rate limit lifts.
        self._blocked: dict[str, datetime] = {}
        #: What GitHub's API last said is left of its hourly allowance, and when it renews.
        self.api_remaining: int | None = None
        self.api_resets_at: datetime | None = None

    def blocked_until(self, host: str) -> datetime | None:
        """When RAVIS may ask `host` again, while it is rate-limiting RAVIS; else None."""
        until = self._blocked.get(host)
        if until is None or until <= self._now():
            self._blocked.pop(host, None)
            return None
        return until

    async def get(self, url: str, *, hosts: frozenset[str], most_bytes: int,
                  accept: str | None = None, readable: frozenset[int] = frozenset()) -> Fetched:
        """A GET under every rule above: `NotFoundError`, `RateLimitedError`, `UnreachableError`
        or `RefusedError` when it can't be answered with a status in the 200s, or in `readable`:
        statuses whose answer the caller reads itself, under the same size cap (skills.sh says
        why it refused a search in a 400's body, RAVIS 0.28.1). A rate-limiting status never
        belongs there."""
        _allowed(url, hosts, "not_https")
        headers = {"Accept": accept} if accept else {}
        try:
            async with asyncio.timeout(TOTAL_SECONDS):
                return await self._follow(url, hosts, most_bytes, headers, readable)
        except TimeoutError:
            raise UnreachableError(f"{host_of(url)} took longer than {TOTAL_SECONDS:.0f} seconds "
                                   "to answer") from None
        except httpx.HTTPError as failure:
            raise UnreachableError(f"RAVIS couldn't reach {host_of(url)} "
                                   f"({type(failure).__name__})") from None

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        """One client per event loop, so connections are reused; a test may run several loops."""
        loop = asyncio.get_running_loop()
        if self._http is None or self._loop is not loop:
            self._http = httpx.AsyncClient(
                transport=self._transport, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(READ_SECONDS, connect=CONNECT_SECONDS),
                headers={"User-Agent": USER_AGENT},
            )
            self._loop = loop
        return self._http

    async def _follow(self, url: str, hosts: frozenset[str], most_bytes: int,
                      headers: dict[str, str], readable: frozenset[int]) -> Fetched:
        for _ in range(MOST_REDIRECTS + 1):
            host = host_of(url) or ""
            until = self.blocked_until(host)
            if until is not None:
                raise RateLimitedError(host, until)
            async with self._client().stream("GET", url, headers=headers) as response:
                self._note_allowance(host, response)
                if response.status_code in REDIRECTS:
                    url = urljoin(url, response.headers.get("location", ""))
                    _allowed(url, hosts, "redirect_elsewhere", came_from=host)
                    continue
                if response.status_code not in readable:
                    self._check_status(host, response)
                body = await _read_at_most(response, most_bytes, host)
                return Fetched(url, response.status_code, response.headers, body)
        raise RefusedError("too_many_redirects",
                           f"{host_of(url)} redirected RAVIS more than {MOST_REDIRECTS} times")

    def _check_status(self, host: str, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        until = self._rate_limit(response)
        if until is not None:
            self._blocked[host] = until
            raise RateLimitedError(host, until)
        # 422 too: GitHub's answer to a branch, tag or commit it doesn't have.
        if status in (401, 403, 404, 410, 422, 451):
            raise NotFoundError(f"{host} has nothing RAVIS may read at that address "
                                f"(HTTP {status})")
        raise UnreachableError(f"{host} answered HTTP {status}")

    def _rate_limit(self, response: httpx.Response) -> datetime | None:
        """When a rate-limiting answer says the limit lifts; None for any other answer."""
        if response.status_code not in (403, 429):
            return None
        said = _retry_after(response.headers.get("retry-after"), self._now())
        if said is not None:
            return said
        if response.headers.get("x-ratelimit-remaining") == "0":
            return _epoch(response.headers.get("x-ratelimit-reset")) or self._now() + UNSAID_WAIT
        return self._now() + UNSAID_WAIT if response.status_code == 429 else None

    def _note_allowance(self, host: str, response: httpx.Response) -> None:
        if host != GITHUB_API:
            return
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining is not None and remaining.isdigit():
            self.api_remaining = int(remaining)
            self.api_resets_at = _epoch(response.headers.get("x-ratelimit-reset"))


def _allowed(url: str, hosts: frozenset[str], reason: str, came_from: str = "") -> None:
    """Refuse a URL that isn't `https`, names a user or password, or isn't on an allowed host.

    `reason` is `redirect_elsewhere` for a redirect's target, which `came_from` sent RAVIS to.
    """
    host = host_of(url)
    if host is None:
        why = (f"{came_from} sent RAVIS on to an address that isn't plain https"
               if came_from else "RAVIS only fetches plain https addresses")
        raise RefusedError("redirect_elsewhere" if came_from else "not_https", why)
    if host in hosts:
        return
    if came_from:
        raise RefusedError(reason, f"{came_from} sent RAVIS on to {host}, which this source "
                                   "doesn't allow")
    raise RefusedError("host_not_allowed", f"{host} isn't a host this source may fetch from")


async def _read_at_most(response: httpx.Response, most_bytes: int, host: str) -> bytes:
    """The body, refused as soon as it passes `most_bytes`, whatever `content-length` claims."""
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > most_bytes:
        raise RefusedError("too_large", _too_large(host, most_bytes))
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > most_bytes:
            raise RefusedError("too_large", _too_large(host, most_bytes))
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large(host: str, most_bytes: int) -> str:
    return f"{host} answered with more than {_size(most_bytes)}, more than RAVIS reads"


def _size(count: int) -> str:
    return f"{count // (1024 * 1024)} MB" if count >= 1024 * 1024 else f"{count // 1024} KB"


def _retry_after(value: str | None, now: datetime) -> datetime | None:
    """`retry-after` as a moment: a number of seconds, or an HTTP date."""
    if not value:
        return None
    if value.strip().isdigit():
        return now + timedelta(seconds=int(value.strip()))
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _epoch(value: str | None) -> datetime | None:
    if value is None or not value.strip().isdigit():
        return None
    return datetime.fromtimestamp(int(value.strip()), UTC)
