"""The skill store's one door to the internet (`agent/skill_web.py`, RAVIS 0.28.0).

What RAVIS is held to, before and while anything is fetched:

- **`https` only, to the hosts a call allows** — nothing is sent otherwise, and a URL naming a user,
  a password or another port is refused;
- **redirects only within those hosts**, never to plain http, at most five;
- **size caps** whether or not an answer says its length;
- **rate limits respected**: the time a limit lifts is kept and nothing is sent to that host until
  then; GitHub's remaining allowance is tracked;
- **statuses and failures in plain words**, **no credential ever sent**, the environment not
  trusted, and **no test in the suite reaching the network**.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from tests.skill_store_rig import START, Clock

from ravis.agent.skill_web import (
    GITHUB_HOSTS,
    MOST_REDIRECTS,
    NotFoundError,
    RateLimitedError,
    RefusedError,
    UnreachableError,
    Web,
    host_of,
)

API = "https://api.github.com"


class Seen:
    """The fake server: every request it was sent, answered by `answer`."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = answer

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)


def web_on(answer: Callable[[httpx.Request], httpx.Response], clock: Clock | None = None
           ) -> tuple[Web, Seen]:
    seen = Seen(answer)
    return Web(transport=httpx.MockTransport(seen), now=clock or Clock()), seen


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"ok")


async def get(web: Web, url: str, most: int = 1024) -> bytes:
    return (await web.get(url, hosts=GITHUB_HOSTS, most_bytes=most)).body


@pytest.mark.parametrize("url, reason", [
    ("http://api.github.com/x", "not_https"),
    ("ftp://api.github.com/x", "not_https"),
    ("https://user:secret@api.github.com/x", "not_https"),
    ("https://evil.example/x", "host_not_allowed"),
    ("https://api.github.com:8443/x", "host_not_allowed"),
])
async def test_only_https_to_an_allowed_host_is_fetched_and_nothing_else_is_sent(
    url: str, reason: str,
) -> None:
    web, seen = web_on(ok)

    with pytest.raises(RefusedError) as caught:
        await get(web, url)

    assert caught.value.reason == reason and seen.requests == []
    assert await get(web, f"{API}/x") == b"ok"


def redirecting(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    moves = {"/renamed": "https://api.github.com/repositories/1", "/relative": "/repositories/1",
             "/elsewhere": "https://evil.example/x", "/downgrade": "http://api.github.com/x"}
    if path in moves:
        return httpx.Response(301, headers={"location": moves[path]})
    if path.startswith("/loop"):
        return httpx.Response(302, headers={"location": f"/loop{int(path[5:] or 0) + 1}"})
    return httpx.Response(200, content=b"moved")


async def test_redirects_are_followed_only_within_the_hosts_a_call_allows() -> None:
    web, seen = web_on(redirecting)

    assert await get(web, f"{API}/renamed") == b"moved"
    assert await get(web, f"{API}/relative") == b"moved"
    for path in ("/elsewhere", "/downgrade"):
        with pytest.raises(RefusedError) as caught:
            await get(web, f"{API}{path}")
        assert caught.value.reason == "redirect_elsewhere"
    assert all(request.url.host == "api.github.com" and request.url.scheme == "https"
               for request in seen.requests)


async def test_a_redirect_loop_stops_after_the_most_redirects() -> None:
    web, seen = web_on(redirecting)

    with pytest.raises(RefusedError) as caught:
        await get(web, f"{API}/loop")

    assert caught.value.reason == "too_many_redirects"
    assert len(seen.requests) == MOST_REDIRECTS + 1


async def test_an_answer_past_its_cap_is_refused_whether_or_not_it_says_its_length() -> None:
    said, _ = web_on(lambda _: httpx.Response(200, content=b"x" * 11))
    unsaid, _ = web_on(lambda _: httpx.Response(200, stream=httpx.ByteStream(b"x" * 11)))

    for web in (said, unsaid):
        with pytest.raises(RefusedError) as caught:
            await get(web, f"{API}/big", most=10)
        assert caught.value.reason == "too_large"
    assert await get(said, f"{API}/big", most=11) == b"x" * 11


async def test_a_rate_limit_is_respected_until_it_lifts_and_nothing_is_sent_meanwhile() -> None:
    clock = Clock()
    lifts = START + timedelta(minutes=12)
    limited = {"now": True}

    def answer(_: httpx.Request) -> httpx.Response:
        headers = {"x-ratelimit-reset": str(int(lifts.timestamp()))}
        if limited["now"]:
            return httpx.Response(403, headers={**headers, "x-ratelimit-remaining": "0"})
        return httpx.Response(200, content=b"ok",
                              headers={**headers, "x-ratelimit-remaining": "59"})

    web, seen = web_on(answer, clock)
    for _ in range(2):
        with pytest.raises(RateLimitedError) as caught:
            await get(web, f"{API}/x")
        assert (caught.value.host, caught.value.retry_at) == ("api.github.com", lifts)
    assert len(seen.requests) == 1 and web.blocked_until("api.github.com") == lifts

    limited["now"] = False
    clock.advance(minutes=13)

    assert await get(web, f"{API}/x") == b"ok"
    assert (web.api_remaining, web.api_resets_at) == (59, lifts)


async def test_retry_after_says_when_and_a_bare_429_waits_a_minute() -> None:
    clock = Clock()
    told, _ = web_on(lambda _: httpx.Response(429, headers={"retry-after": "120"}), clock)
    bare, _ = web_on(lambda _: httpx.Response(429), clock)

    with pytest.raises(RateLimitedError) as said:
        await get(told, f"{API}/x")
    with pytest.raises(RateLimitedError) as unsaid:
        await get(bare, f"{API}/x")

    assert said.value.retry_at == START + timedelta(seconds=120)
    assert unsaid.value.retry_at == START + timedelta(seconds=60)


@pytest.mark.parametrize("status, error", [
    (404, NotFoundError), (422, NotFoundError), (403, NotFoundError), (500, UnreachableError),
    (418, UnreachableError),
])
async def test_each_status_is_a_failure_in_plain_words(status: int, error: type[Exception]
                                                       ) -> None:
    web, _ = web_on(lambda _: httpx.Response(status))

    with pytest.raises(error):
        await get(web, f"{API}/x")


async def test_a_network_failure_is_unreachable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    web, _ = web_on(fail)

    with pytest.raises(UnreachableError, match="couldn't reach api.github.com"):
        await get(web, f"{API}/x")


async def test_no_credential_is_sent_and_the_environment_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine api.github.com login owner password a-token\n")
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    web, seen = web_on(ok)

    await get(web, f"{API}/x")

    assert "authorization" not in seen.requests[0].headers
    assert seen.requests[0].headers["user-agent"].startswith("RAVIS-skill-store")
    assert web._client().trust_env is False


async def test_a_web_built_without_a_transport_of_its_own_cant_reach_the_network_in_tests() -> None:
    with pytest.raises(AssertionError, match="reached the network"):
        await get(Web(), f"{API}/x")


def test_a_host_as_the_allow_lists_name_it() -> None:
    assert host_of("https://API.GitHub.com/x") == "api.github.com"
    assert host_of("https://example.com:443/") == "example.com"
    assert host_of("https://example.com:8443/") == "example.com:8443"
    assert host_of("http://example.com/") is None
    assert host_of("https://owner@example.com/") is None
    assert host_of("https://[::1/") is None
