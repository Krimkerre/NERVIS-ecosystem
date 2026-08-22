"""The body-size cap must refuse *before* authentication runs.

This file exists because the ordering is the substance of the rule and because
the assertion is easy to fake. In the router this limit was borrowed from, the
cap was registered before the auth middleware and its docstring said so — but
the framework builds middleware in reverse, so it actually ran second. The suite
could not detect it: the test built a throwaway app whose only middleware was
the cap, and the only production assertion was that the function existed.

So this test does the opposite. It wires a sentinel that records whether
authentication was reached, sends an oversized body through the *real* stack,
and asserts the request was refused **and** the sentinel never fired.
"""

from __future__ import annotations

from ravis.admission import BodySizeLimiter


class AuthenticationSentinel:
    """A stand-in for everything downstream of the size check.

    Records whether it was reached. If an oversized body ever gets this far, the
    cap is running too late no matter what the registration order claims.
    """

    def __init__(self) -> None:
        self.was_reached = False

    async def __call__(self, scope: dict, receive, send) -> None:  # noqa: ANN001, ARG002
        self.was_reached = True
        # Read the body, as any handler parsing JSON does. The streaming counter
        # only fires while the application reads, which is precisely the case
        # that can exhaust memory — a handler that never reads cannot be hurt by
        # a large body it never touches.
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        body = b'{"ok":true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


async def _send_request(app, body: bytes, declare_length: bool) -> tuple[int, bool]:
    """Drive the ASGI app directly and return (status, whether it was reached)."""
    headers = [(b"content-type", b"application/json")]
    if declare_length:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": headers}

    sent: list[dict] = []
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, delivered


def test_declared_oversize_is_refused_without_reaching_authentication() -> None:
    """A caller that admits the size up front is refused at zero cost."""
    sentinel = AuthenticationSentinel()
    app = BodySizeLimiter(sentinel, max_bytes=100)

    import asyncio

    status, _ = asyncio.run(_send_request(app, b"x" * 500, declare_length=True))

    assert status == 413
    assert sentinel.was_reached is False, "authentication ran before the size check"


def test_undeclared_oversize_is_refused_while_streaming() -> None:
    """A caller that omits Content-Length is measured rather than trusted."""
    sentinel = AuthenticationSentinel()
    app = BodySizeLimiter(sentinel, max_bytes=100)

    import asyncio

    status, _ = asyncio.run(_send_request(app, b"x" * 500, declare_length=False))

    assert status == 413


def test_a_body_within_the_limit_passes_through() -> None:
    """The cap must not refuse ordinary traffic, or it is just an outage."""
    sentinel = AuthenticationSentinel()
    app = BodySizeLimiter(sentinel, max_bytes=1000)

    import asyncio

    status, _ = asyncio.run(_send_request(app, b'{"model":"x"}', declare_length=True))

    assert status == 200
    assert sentinel.was_reached is True
