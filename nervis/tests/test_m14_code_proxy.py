"""§13.3's proxy gate: the ten tests M14 names, and the rules underneath them.

**Every test here drives the real application.** A proxy is a boundary, and a
boundary asserted against its own helper functions is a boundary nobody has
crossed. The upstream is an in-process fake (runbook §14.5) so that "code-server
answered" means exactly that and not "something on this machine was listening".

§13.3's gate is quoted so the list can be checked against it rather than
remembered: *"unauthorized, wrong-origin, CSRF, traversal, open-redirect,
malicious upstream, stale token, WebSocket reconnect, large stream and teardown
tests all pass"*.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
import websockets
from fastapi.testclient import TestClient

from nervis.api.code import SECURITY_HEADERS
from nervis.app import create_app
from nervis.code_proxy import COOKIE, IDLE_SECONDS, SESSION_SECONDS, Sessions
from nervis.config import Settings
from nervis.errors import UnauthorizedError

#: A literal address, not a name: the registry refuses a named host as an
#: SSRF precaution, and a fixture that trips an unrelated guard makes every
#: failure here start with a question about the fixture.
UPSTREAM = "http://127.0.0.1:65000"

#: Upgrade headers the fake editor saw, newest last.
SEEN_HEADERS: list[dict[str, str]] = []


class FakeEditor:
    """code-server, as far as the proxy can tell, plus a record of what it saw.

    `seen` is the assertion surface for the forwarding rules: whether NERVIS's
    own cookie reached the editor is a question only the editor can answer, and
    reading it out of NERVIS's own code would let one bug hide another.
    """

    def __init__(self, **scripted: Any) -> None:
        self.seen: list[httpx.Request] = []
        self.scripted = scripted

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path in self.scripted:
            status, headers, body = self.scripted[path]
            return httpx.Response(status, headers=headers, stream=_Chunks([body]))
        if path == "/big":
            # In chunks, because that is what a real bundle arrives as and it
            # is the shape the streaming test is actually about.
            return httpx.Response(200, stream=_Chunks([b"x" * 64 * 1024] * 32))
        return httpx.Response(
            200, headers={"content-type": "text/html"}, stream=_Chunks([b"editor"])
        )


class _Chunks(httpx.AsyncByteStream):
    """A response body that arrives over the wire rather than all at once.

    `httpx.Response(content=...)` builds one already read, and `aiter_raw` on
    it raises `StreamConsumed` — so a fake built that way cannot be proxied by
    code that streams, and the failure looks like a proxy bug rather than a
    fixture that does not behave like a socket.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            yield chunk


def an_app(tmp_path: Path, editor: FakeEditor, **overrides: Any) -> TestClient:
    settings = Settings(
        database_path=str(tmp_path / "nervis.db"),
        _env_file=None,  # type: ignore[call-arg]
        **{
            "code_server_base_url": UPSTREAM,
            "code_workspace_roots": str(tmp_path),
            **overrides,
        },
    )
    app = create_app(settings)
    client = TestClient(app)
    app.state.code_client = httpx.AsyncClient(
        transport=editor.transport(), follow_redirects=False
    )
    return client


def control(client: TestClient) -> dict[str, str]:
    return {"x-nervis-control": client.app.state.control_token}


def open_session(client: TestClient, tmp_path: Path) -> httpx.Response:
    return client.post(
        "/api/v1/code/session", json={"workspace": str(tmp_path)}, headers=control(client)
    )


# ── The gate, in §13.3's own order ──────────────────────────────────────────


def test_unauthorized_a_request_with_no_session_is_refused(tmp_path: Path) -> None:
    """No session, no editor. The first of the ten, and the one the other nine
    would be decoration without."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        answered = client.get("/code/")

        assert answered.status_code == 401
        assert editor.seen == [], "an unauthorised request reached the editor"


def test_wrong_origin_a_cross_site_mutation_never_reaches_the_editor(
    tmp_path: Path,
) -> None:
    """A page on another origin may issue the request; it may not have it acted
    on. Refused by the same middleware every other mutating route here uses,
    which is the point — the proxy does not get its own weaker copy."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        answered = client.post("/code/anything", headers={"sec-fetch-site": "cross-site"})

        assert answered.status_code == 403
        assert editor.seen == []


def test_csrf_opening_a_session_needs_the_page_s_own_token(tmp_path: Path) -> None:
    """Minting the credential is the mutation worth protecting. Without the
    control token the request carries nothing but the browser's willingness to
    send it, which is the definition of the ambient authority CSRF exploits."""
    with an_app(tmp_path, FakeEditor()) as client:
        answered = client.post("/api/v1/code/session", json={"workspace": str(tmp_path)})

        assert answered.status_code == 403
        assert COOKIE not in answered.cookies


@pytest.mark.parametrize(
    "path",
    ["../etc/passwd", "static/../../etc/passwd", "..%2fetc", "c:\\windows", "%2e%2e/etc"],
    ids=["climb", "climb_mid_path", "encoded_climb", "backslash", "encoded_dots"],
)
def test_traversal_a_path_that_leaves_the_editor_is_refused(
    tmp_path: Path, path: str
) -> None:
    """**The assertion is that the editor never sees it**, not which number
    comes back.

    Two different mechanisms refuse these and both are correct: a client or
    router that normalises `..` away leaves a path matching no route at all
    (404), and anything that survives normalisation — percent-encoded dots, a
    backslash that is a separator on one platform and a literal on another —
    meets `_target` and is refused outright (409). Asserting one status would
    be asserting which of the two happened to act first, and that is not the
    property worth holding.
    """
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        answered = client.get(f"/code/{path}")

        assert answered.status_code in {404, 409}, answered.text
        assert editor.seen == [], f"{path} reached the editor"


def test_open_redirect_a_location_off_the_upstream_is_dropped(tmp_path: Path) -> None:
    """The header the whole risk lives in.

    A `Location` pointing off the machine is reflected by NERVIS's own origin,
    so following it means leaving with NERVIS's address in the URL bar. The
    redirect is dropped rather than passed on, which breaks that redirect and
    is the correct trade.
    """
    editor = FakeEditor(**{
        "/away": (302, {"location": "https://evil.example/steal"}, b""),
    })
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        answered = client.get("/code/away", follow_redirects=False)

        assert answered.status_code == 302
        assert "location" not in {k.lower() for k in answered.headers}


def test_a_redirect_inside_the_editor_is_rewritten_onto_the_proxy(tmp_path: Path) -> None:
    """The other half of the same rule, without which dropping is over-broad:
    the editor's own redirects have to keep working, on NERVIS's path."""
    editor = FakeEditor(**{
        "/go": (302, {"location": f"{UPSTREAM}/workbench.html"}, b""),
    })
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        answered = client.get("/code/go", follow_redirects=False)

        assert answered.headers["location"] == "/code/workbench.html"


def test_malicious_upstream_nothing_in_a_request_can_move_the_target(
    tmp_path: Path,
) -> None:
    """The difference between a proxy and an open relay.

    Every shape that classically redirects a naive proxy is tried: an absolute
    URL as the path, a scheme-relative one, and a `Host` header claiming to be
    somewhere else. The upstream comes from configuration, so all three are
    either refused or land on the configured host regardless.
    """
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        # An absolute URL and a scheme-relative one both replace the base
        # under ordinary URL joining. Neither is a path, so neither is one the
        # editor could have asked for.
        assert client.get("/code/http://evil.example/x").status_code == 409
        assert client.get("/code//evil.example/x").status_code == 409
        # A forged `Host` never reaches this route at all: NERVIS's own
        # allow-list refuses it in middleware, which is the DNS-rebinding
        # defence doing double duty here. Asserted rather than assumed,
        # because "something else already stops it" is exactly the belief that
        # leaves a hole when the something else moves.
        assert client.get("/code/ok", headers={"host": "evil.example"}).status_code == 403
        # And the request that *is* allowed lands on the configured upstream.
        assert client.get("/code/ok").status_code == 200
        assert all(request.url.host == "127.0.0.1" for request in editor.seen)
        assert [request.url.path for request in editor.seen] == ["/ok"]


def test_stale_token_an_expired_session_stops_working(tmp_path: Path) -> None:
    """Enforced when presented, not swept on a timer: a token must not be valid
    for the gap between its deadline and the next sweep."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)
        assert client.get("/code/").status_code == 200

        # Time passes. The store owns the clock, so this is the same expiry the
        # real one applies rather than a flag the test invented.
        clock = [1000.0]
        client.app.state.code_sessions = Sessions(now=lambda: clock[0])
        session = client.app.state.code_sessions.open(str(tmp_path), [str(tmp_path)])
        client.cookies.set(COOKIE, session.token)
        clock[0] += IDLE_SECONDS + 1

        answered = client.get("/code/")

        assert answered.status_code == 401
        assert "idle" in answered.json()["error"]["message"]


def test_a_session_also_ends_at_its_maximum_age(tmp_path: Path) -> None:
    """Both timeouts, because §13.3 asks for both. A session kept alive by its
    own traffic is still one nobody re-authorised."""
    clock = [1000.0]
    sessions = Sessions(now=lambda: clock[0])
    session = sessions.open(str(tmp_path), [str(tmp_path)])

    # Busy the whole time: never idle, and still over.
    for _ in range(20):
        clock[0] += SESSION_SECONDS / 20
        # The last iteration is the one that goes over; the assertion below is
        # what reads the outcome, so the refusal itself is not the subject here.
        with contextlib.suppress(UnauthorizedError):
            sessions.holding(session.token)

    assert sessions.state(session.token)["open"] is False


def test_websocket_reconnect_a_dropped_socket_can_be_opened_again(
    tmp_path: Path, editor_socket: str
) -> None:
    """The connection the editor does its actual work over.

    Two consecutive sockets on one session, the first closed from the browser
    side. A proxy that leaked the upstream connection or left the session in a
    half-open state would fail the second.
    """
    editor = FakeEditor()
    with an_app(tmp_path, editor, code_server_base_url=editor_socket) as client:
        open_session(client, tmp_path)

        for attempt in ("first", "second"):
            with client.websocket_connect("/code/socket") as socket:
                socket.send_text(attempt)
                assert socket.receive_text() == f"echo:{attempt}"


def test_the_browser_s_headers_reach_the_editor_on_the_upgrade(
    tmp_path: Path, editor_socket: str
) -> None:
    """**The handshake carries the cookie, or the editor never authorises it.**

    code-server checks its own session on the upgrade, and a socket opened with
    no headers is one it will not complete. The symptom is not an error: the
    workbench renders an empty frame and reports `ETIMEDOUT` from its own side,
    which reads as a network fault. So what is asserted here is that the
    editor's own cookie arrives — and that NERVIS's does not, which is the same
    rule the request path follows.
    """
    editor = FakeEditor()
    with an_app(tmp_path, editor, code_server_base_url=editor_socket) as client:
        open_session(client, tmp_path)
        client.cookies.set("code-server-session", "theirs")

        with client.websocket_connect("/code/socket") as socket:
            socket.send_text("hello")
            assert socket.receive_text() == "echo:hello"

    seen = SEEN_HEADERS[-1]
    assert "code-server-session=theirs" in seen.get("cookie", "")
    assert COOKIE not in seen.get("cookie", "")
    # **The browser's own authority, handed over separately.** code-server
    # compares `Origin` against its host and refuses the upgrade when they
    # differ — which through a proxy they always do. Without this the editor
    # answers 403, the workbench renders an empty frame, and the only visible
    # complaint is a timeout on the browser's side.
    assert seen["x-forwarded-host"] == "testserver"
    assert seen["x-forwarded-proto"] == "http"
    # The library writes these itself; a forwarded copy makes two of each.
    assert seen.get("sec-websocket-key") is not None
    assert seen["host"] == editor_socket.split("//", 1)[1]


def test_a_websocket_without_a_session_never_reaches_the_editor(
    tmp_path: Path, editor_socket: str
) -> None:
    """A WebSocket is not a lesser request. Refused before the handshake
    completes, so the browser sees a failed connection rather than one that
    opens and dies for no stated reason."""
    from starlette.websockets import WebSocketDisconnect

    editor = FakeEditor()
    with an_app(tmp_path, editor, code_server_base_url=editor_socket) as client:
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            client.websocket_connect("/code/socket"),
        ):
            pass

        assert refused.value.code == 1008


def test_large_stream_a_big_response_is_carried_without_being_buffered(
    tmp_path: Path,
) -> None:
    """An editor's first load is megabytes of bundle. Read in chunks and
    checked whole: the assertion is that all of it arrives, and the streaming
    is what stops a tab costing its own size in NERVIS's memory."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        with client.stream("GET", "/code/big") as answered:
            received = sum(len(chunk) for chunk in answered.iter_bytes())

        assert answered.status_code == 200
        assert received == 2 * 1024 * 1024


def test_teardown_closing_a_session_stops_the_editor_answering(tmp_path: Path) -> None:
    """§13.3's last named test, and the one a person actually performs: they
    close the tab and expect the credential to stop working."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)
        assert client.get("/code/").status_code == 200

        closed = client.request("DELETE", "/api/v1/code/session", headers=control(client))

        assert closed.status_code == 204
        assert client.get("/code/").status_code == 401


# ── The rules the gate rests on ─────────────────────────────────────────────


def test_a_workspace_outside_every_configured_root_is_refused(tmp_path: Path) -> None:
    """§13.3's "explicit workspace selection", and the line it draws: NERVIS
    provides connectivity, not an alternate filesystem authority."""
    with an_app(tmp_path, FakeEditor()) as client:
        answered = client.post(
            "/api/v1/code/session", json={"workspace": "/etc"}, headers=control(client)
        )

        assert answered.status_code == 422
        assert "workspace root" in answered.json()["error"]["message"]


def test_a_sibling_directory_that_shares_a_prefix_is_not_inside_the_root(
    tmp_path: Path,
) -> None:
    """`/home/me/work-secrets` starts with `/home/me/work`. A prefix test on
    text admits it; comparing paths does not."""
    root = tmp_path / "work"
    root.mkdir()
    sibling = tmp_path / "work-secrets"
    sibling.mkdir()
    with an_app(tmp_path, FakeEditor(), code_workspace_roots=str(root)) as client:
        answered = client.post(
            "/api/v1/code/session", json={"workspace": str(sibling)}, headers=control(client)
        )

        assert answered.status_code == 422


def test_nervis_s_own_credentials_never_reach_the_editor(tmp_path: Path) -> None:
    """Redaction, in the direction that matters: the editor has no use for
    NERVIS's session cookie or control token, and an upstream that logged its
    request headers would be logging both."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        client.get("/code/", headers={**control(client), "authorization": "Bearer secret"})

        sent = editor.seen[-1].headers
        assert COOKIE not in sent.get("cookie", "")
        assert "x-nervis-control" not in sent
        assert "authorization" not in sent


def test_a_client_cannot_choose_the_forwarded_host(tmp_path: Path) -> None:
    """The header that decides an origin check is not one a request supplies.

    NERVIS's own `Host` allow-list has already vetted what gets passed on, so
    what reaches the editor is a host this deployment admits. A caller-supplied
    `X-Forwarded-Host` that survived would let anything able to reach the port
    tell code-server that an origin it should refuse is one it should trust.
    """
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)

        client.get("/code/", headers={"x-forwarded-host": "evil.example",
                                      "x-forwarded-proto": "https"})

        sent = editor.seen[-1].headers
        assert sent["x-forwarded-host"] == "testserver"
        assert sent["x-forwarded-proto"] == "http"


def test_the_editor_s_own_cookies_do_reach_it(tmp_path: Path) -> None:
    """The other half: code-server keeps its own session, and a proxy that ate
    every cookie would log the user out on every request."""
    editor = FakeEditor()
    with an_app(tmp_path, editor) as client:
        open_session(client, tmp_path)
        client.cookies.set("code-server-session", "theirs")

        client.get("/code/")

        assert "code-server-session=theirs" in editor.seen[-1].headers.get("cookie", "")


def test_every_proxied_response_carries_the_frame_and_sniffing_rules(
    tmp_path: Path,
) -> None:
    """§13.3's browser security headers. Asserted on the response the browser
    actually receives, because a header added to the wrong object is the way
    this is usually got wrong."""
    with an_app(tmp_path, FakeEditor()) as client:
        open_session(client, tmp_path)

        answered = client.get("/code/")

        for name, value in SECURITY_HEADERS.items():
            assert answered.headers[name] == value


def test_the_session_state_is_readable_and_never_carries_the_token(
    tmp_path: Path,
) -> None:
    """§13.3's "visible connection state" — and the one thing it must not show."""
    with an_app(tmp_path, FakeEditor()) as client:
        open_session(client, tmp_path)

        state = client.get("/api/v1/code/session").json()["session"]

        assert state["open"] is True
        assert state["workspace"] == str(tmp_path)
        assert 0 < state["idle_seconds_remaining"] <= IDLE_SECONDS
        assert "token" not in state


def test_a_closed_session_reads_as_closed_rather_than_erroring(tmp_path: Path) -> None:
    """A tab asking what is going on gets an answer, not a 401. "Nothing is
    open" is a complete state to render — and it carries what may be opened, so
    the choice the page offers comes from configuration rather than from a
    path somebody types."""
    with an_app(tmp_path, FakeEditor()) as client:
        state = client.get("/api/v1/code/session").json()["session"]

        assert state["open"] is False
        assert state["reason"]
        assert state["roots"] == [str(tmp_path)]


def test_an_open_session_does_not_advertise_the_roots(tmp_path: Path) -> None:
    """Once a workspace is chosen the menu is not the answer to the question,
    and every field on a state payload is one more thing a screen can show by
    accident."""
    with an_app(tmp_path, FakeEditor()) as client:
        open_session(client, tmp_path)

        state = client.get("/api/v1/code/session").json()["session"]

        assert state["open"] is True
        assert "roots" not in state


@pytest.fixture
def editor_socket() -> Iterator[str]:
    """A real WebSocket server standing in for code-server.

    Real rather than mocked because the thing under test is the bridge itself —
    the handshake, both directions, and the close — and a fake that skipped the
    handshake would skip the half most likely to be wrong.
    """
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    port: list[int] = []
    # **Shut down by resolving a future rather than by stopping the loop.**
    # Stopping it mid-`serve` leaves the server's own `close` half-run as a
    # coroutine nobody awaits, which pytest reports as an unraisable exception
    # against whichever test happened to be running — a fixture's mess
    # attributed to somebody else's assertion.
    finished: list[asyncio.Future[None]] = []

    async def echo(socket: Any) -> None:
        # What the upgrade carried, for the test that asserts on it. Recorded
        # at the editor rather than read out of NERVIS: whether a header
        # arrived is a question only the far end can answer.
        SEEN_HEADERS.append({k.lower(): v for k, v in socket.request.headers.raw_items()})
        async for message in socket:
            await socket.send(f"echo:{message}")

    async def serve() -> None:
        async with websockets.serve(echo, "127.0.0.1", 0) as server:
            port.append(server.sockets[0].getsockname()[1])
            finished.append(asyncio.get_running_loop().create_future())
            ready.set()
            await finished[0]

    def run() -> None:
        try:
            loop.run_until_complete(serve())
        finally:
            loop.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(timeout=5)
    yield f"http://127.0.0.1:{port[0]}"
    loop.call_soon_threadsafe(lambda: finished[0].set_result(None))
    thread.join(timeout=5)


# ── §13.5: the settings, and the extension NERVIS may install ───────────────


def test_the_tab_being_switched_off_closes_the_proxy_too(tmp_path: Path) -> None:
    """**Off has to mean the route, not only the screen.**

    A switch that hid a tab while leaving `/code/` serving would be off in the
    one place that cannot reach the editor anyway and on in the place anything
    else can.
    """
    editor = FakeEditor()
    with an_app(tmp_path, editor, code_tab_enabled=False) as client:
        opened = client.post(
            "/api/v1/code/session", json={"workspace": str(tmp_path)}, headers=control(client)
        )
        proxied = client.get("/code/")
        state = client.get("/api/v1/code/session").json()["session"]

        assert opened.status_code == 409
        assert proxied.status_code in {401, 409}
        assert state["open"] is False
        assert "switched off" in state["reason"]
        assert editor.seen == []


def a_vsix(path: Path, *, publisher: str = "krimkerre", name: str = "clarvis",
           version: str = "0.13.0") -> Path:
    """A real `.vsix` — a zip with the extension's own manifest inside it.

    Real rather than mocked because the thing under test is reading a version
    out of the artefact instead of off its filename, and a fake that handed
    back a version would skip exactly that.
    """
    import zipfile

    package = path / f"{name}-{version}.vsix"
    with zipfile.ZipFile(package, "w") as bundle:
        bundle.writestr(
            "extension/package.json",
            f'{{"publisher": "{publisher}", "name": "{name}", "version": "{version}"}}',
        )
    return package


def test_the_offered_version_is_read_from_the_package_not_its_name(
    tmp_path: Path,
) -> None:
    """A rebuild that keeps the filename changes everything except the filename."""
    from nervis.code_extension import offered

    package = a_vsix(tmp_path, version="0.13.0")
    renamed = tmp_path / "clarvis-9.9.9.vsix"
    package.rename(renamed)

    assert offered(str(renamed)).version == "0.13.0"
    assert offered(str(renamed)).identifier == "krimkerre.clarvis"


def test_a_package_that_is_not_clarvis_is_refused(tmp_path: Path) -> None:
    """NERVIS installs one extension. A setting that could name any `.vsix` and
    a verb that installed whatever it found would be a way to put arbitrary
    software into the editor by editing a config line."""
    from nervis.code_extension import ExtensionError, install

    other = a_vsix(tmp_path, publisher="someone", name="else")

    with pytest.raises(ExtensionError) as refused:
        install("/bin/echo", str(other))

    assert "krimkerre.clarvis" in str(refused.value)


def test_an_unreadable_package_says_so_rather_than_raising_a_zip_error(
    tmp_path: Path,
) -> None:
    """The message a person can act on, rather than the one the library chose."""
    from nervis.code_extension import ExtensionError, offered

    broken = tmp_path / "broken.vsix"
    broken.write_bytes(b"not a zip")

    with pytest.raises(ExtensionError) as refused:
        offered(str(broken))

    assert "not a readable .vsix" in str(refused.value)


def test_what_is_due_says_which_of_the_two_reasons_it_is(tmp_path: Path) -> None:
    """"Not installed" and "an older version is installed" call for different
    words on a screen, so the answer is the reason rather than a boolean."""
    from nervis.code_extension import due

    package = a_vsix(tmp_path, version="0.13.0")

    # A stand-in for the editor's CLI: `--list-extensions` is the only read the
    # decision needs, and a fake shell script keeps the subprocess real.
    editor = tmp_path / "code-server"
    editor.write_text("#!/bin/sh\nexit 0\n")
    editor.chmod(0o755)
    assert "is not installed" in due(str(editor), str(package))

    editor.write_text("#!/bin/sh\necho krimkerre.clarvis@0.12.8\n")
    assert "0.12.8" in due(str(editor), str(package))

    editor.write_text("#!/bin/sh\necho krimkerre.clarvis@0.13.0\n")
    assert due(str(editor), str(package)) == ""


def test_the_extension_route_reports_configured_offered_and_installed(
    tmp_path: Path,
) -> None:
    """What the tab renders. Three facts, and the difference between them."""
    package = a_vsix(tmp_path, version="0.13.0")
    editor_cli = tmp_path / "code-server"
    editor_cli.write_text("#!/bin/sh\necho krimkerre.clarvis@0.12.8\n")
    editor_cli.chmod(0o755)
    with an_app(tmp_path, FakeEditor(), clarvis_vsix_path=str(package),
                code_server_binary=str(editor_cli)) as client:
        body = client.get("/api/v1/code/extension").json()["extension"]

        assert body["configured"] is True
        assert body["identifier"] == "krimkerre.clarvis"
        assert body["offered"] == "0.13.0"
        assert body["installed"] == "0.12.8"
        assert "0.12.8" in body["due"]


def test_nothing_configured_is_a_state_rather_than_an_error(tmp_path: Path) -> None:
    """A tab that has not been configured gets an answer it can render, not a
    500 that reads as NERVIS being broken."""
    with an_app(tmp_path, FakeEditor()) as client:
        body = client.get("/api/v1/code/extension").json()["extension"]

        assert body["configured"] is False
        assert "no Clarvis package" in body["detail"]


def test_installing_needs_the_page_s_own_token_and_takes_no_arguments(
    tmp_path: Path,
) -> None:
    """The mutation carries the control token; what it installs is what the
    setting names, so a request cannot choose the file."""
    package = a_vsix(tmp_path)
    calls = tmp_path / "calls.txt"
    editor_cli = tmp_path / "code-server"
    editor_cli.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    editor_cli.chmod(0o755)
    with an_app(tmp_path, FakeEditor(), clarvis_vsix_path=str(package),
                code_server_binary=str(editor_cli)) as client:
        assert client.post("/api/v1/code/extension").status_code == 403

        answered = client.post("/api/v1/code/extension", headers=control(client))

        assert answered.status_code == 201
        assert answered.json()["extension"]["identifier"] == "krimkerre.clarvis"
        assert "--install-extension" in calls.read_text()
        assert "--force" in calls.read_text(), "update must not need an uninstall first"


def test_an_install_that_fails_says_what_the_editor_said(tmp_path: Path) -> None:
    """code-server's own words. "Exit status 1" is not something to act on."""
    package = a_vsix(tmp_path)
    editor_cli = tmp_path / "code-server"
    editor_cli.write_text('#!/bin/sh\necho "Extension is not compatible" >&2\nexit 1\n')
    editor_cli.chmod(0o755)
    with an_app(tmp_path, FakeEditor(), clarvis_vsix_path=str(package),
                code_server_binary=str(editor_cli)) as client:
        answered = client.post("/api/v1/code/extension", headers=control(client))

        assert answered.status_code == 409
        assert "not compatible" in answered.json()["error"]["message"]
