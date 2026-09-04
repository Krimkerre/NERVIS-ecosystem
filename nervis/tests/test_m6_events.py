"""M6 — the event hub (§11.1).

M6's exit: *"Events appear live; RAVIS events ingest; retention is enforced; **an
invalid event cannot crash the hub**."*

The last one is the headline and gets the most tests, because it is the clause a
hub fails silently: a producer sending rubbish is ordinary, and the wrong
response — an exception on the ingestion path — punishes every other producer for
one's mistake.

**"RAVIS events ingest" is tested as the ingestion path accepting a RAVIS-shaped
envelope**, which is what NERVIS owns. RAVIS advertises `ravis.events@1` as
unavailable until M18b, so the producer half does not exist yet and §1 forbids
inventing it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.events import Hub, Rejected, replay_from, sse_frame, validate
from nervis.storage import prepare_database


def envelope(**overrides: Any) -> dict[str, Any]:
    """§4.4's envelope, as RAVIS would send one."""
    return {
        "event_id": overrides.pop("event_id", "01J000000000000000000000AA"),
        "event_type": "ravis.route.selected",
        "event_version": "1.0.0",
        "occurred_at": "2026-08-22T12:00:00Z",
        "source": {"service_type": "ravis", "service_id": "r1", "instance_id": "i1"},
        "subject": {"type": "session", "id": "s1"},
        "trace_id": "t1", "span_id": "sp1", "request_id": "rq1", "session_id": "s1",
        "severity": "info",
        "data": {"selected": "qwen3-4b"},
        "privacy": {"classification": "operational", "redactions": []},
        **overrides,
    }


def a_hub(**kwargs: Any) -> Hub:
    return Hub(prepare_database(":memory:"), **kwargs)


def an_api() -> TestClient:
    return TestClient(create_app(Settings(
        database_path=":memory:",
        ravis_base_url="http://127.0.0.1:9", sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9", lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )))


# ── An invalid event cannot crash the hub ──────────────────────────────────


def test_a_restart_is_not_an_outage() -> None:
    """The sweep can land after uvicorn has begun closing the socket, and NERVIS
    then observes *itself* as unreachable. That reached the hub as a warning and
    came back out of a chat answer as "a ConnectError indicating no response
    from the nervis service" — reported by the process answering the question.
    """
    import asyncio

    from nervis.app import _announce_transitions
    from nervis.registry import RegistryState

    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    api = create_app(settings)
    before = {entry.key: entry.state for entry in api.state.registry.all()}
    for entry in api.state.registry.all():
        entry.state = RegistryState.UNREACHABLE

    api.state.stopping = asyncio.Event()
    api.state.stopping.set()
    _announce_transitions(api, before)

    assert not api.state.hub.query(latest=True, limit=10), "shutdown published events"

    # And with the service running, the same transitions are published.
    api.state.stopping.clear()
    _announce_transitions(api, before)

    assert api.state.hub.query(latest=True, limit=10)


def test_nothing_a_producer_sends_raises() -> None:
    """The headline clause, against everything worth throwing at it.

    A hub that raised here would take the ingestion path down for every other
    producer, which is the failure §11.1's gate means by *"without blocking
    producers"*.
    """
    hub = a_hub()
    rubbish: list[Any] = [
        None, 42, "a string", [], {}, {"event_id": ""},
        {"event_id": "x"},                                   # no type, no time
        envelope(data="not an object"),
        envelope(source="not an object"),
        envelope(severity="catastrophic"),
        envelope(occurred_at="   "),
        {"event_id": "y", "event_type": "t", "occurred_at": "now", "data": {"n": float("nan")}},
    ]

    for payload in rubbish:
        outcome = hub.ingest(payload)
        assert isinstance(outcome, Rejected), payload

    # And every one is retrievable with the reason, rather than dropped.
    assert len(hub.quarantined()) == len(rubbish)
    assert all(row["reason"] for row in hub.quarantined())


def test_a_rejected_event_is_kept_rather_than_dropped() -> None:
    """"The hub is quiet" and "a producer is sending rubbish" look identical
    from outside, and need different things done about them."""
    hub = a_hub()

    hub.ingest({"event_id": "x", "event_type": "t"})

    row = hub.quarantined()[0]
    assert row["reason"] == "missing required field(s)"
    assert "occurred_at" in row["detail"]
    assert "event_id" in row["payload"]


def test_ingestion_answers_202_even_for_rubbish() -> None:
    """A rejected event is a fact about the producer, not a failure of the hub.

    Answering 4xx or 5xx would invite a retry of something that cannot parse.
    """
    client = an_api()

    response = client.post("/api/v1/events", json=[envelope(), {"nope": True}])

    # 202, as the name of this test, its docstring, the endpoint's docstring and
    # STATUS.md all say. It asserted 200 -- the FastAPI default, because the
    # route declared no status -- so the one check on the contract was written
    # to match the behaviour instead of the contract, and passed for as long as
    # the endpoint got it wrong.
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"][0]["reason"] == "missing required field(s)"


def test_a_body_that_is_not_json_is_the_one_refusal() -> None:
    """NERVIS's own boundary rather than a producer's envelope: there is nothing
    to quarantine and nothing to describe."""
    client = an_api()

    response = client.post(
        "/api/v1/events", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CONFIGURATION"


def test_an_unknown_optional_field_survives() -> None:
    """§4.2: consumers ignore unknown optional fields.

    A hub that dropped them would be lossy for every event type written after
    it — and §11.1 makes the producer authoritative, not the hub.
    """
    hub = a_hub()

    hub.ingest(envelope(something_new={"from": "a later RAVIS"}))

    assert hub.query()[0]["something_new"] == {"from": "a later RAVIS"}


def test_the_hub_does_not_validate_the_data_payload() -> None:
    """*Event type plus version determines the `data` schema* — the consumer's
    business, not the hub's.

    Validating it here would need the hub to know every event type in the
    ecosystem, which is the coupling the envelope exists to avoid.
    """
    hub = a_hub()

    outcome = hub.ingest(envelope(event_type="something.nobody.declared", data={"any": "shape"}))

    assert not isinstance(outcome, Rejected)


# ── Ingestion, deduplication, replay ───────────────────────────────────────


def test_a_ravis_shaped_envelope_ingests() -> None:
    hub = a_hub()

    hub.ingest(envelope())

    stored = hub.query()[0]
    assert stored["event_type"] == "ravis.route.selected"
    assert stored["source"]["service_type"] == "ravis"
    assert stored["_sequence"] == 1


def test_a_duplicate_is_stored_once_and_not_rebroadcast() -> None:
    """§4.4: consumers tolerate duplicates by `event_id`.

    A producer replaying after a reconnect is doing the right thing, not making
    a mistake — and a subscriber that already saw the event does not need it
    twice.
    """
    hub = a_hub()
    hub.ingest(envelope())
    queue = hub.subscribe()

    hub.ingest(envelope())

    assert len(hub.query()) == 1
    assert queue.empty()


def test_occurred_at_and_received_at_are_both_kept() -> None:
    """§11.2 requires clock skew to be visible, and one timestamp cannot show it."""
    hub = a_hub()

    hub.ingest(envelope(occurred_at="1999-01-01T00:00:00Z"))

    stored = hub.query()[0]
    assert stored["occurred_at"] == "1999-01-01T00:00:00Z"
    assert stored["_received_at"] > "2020"


def test_replay_resumes_from_a_cursor() -> None:
    hub = a_hub()
    for n in range(5):
        hub.ingest(envelope(event_id=f"e{n}"))

    assert [e["event_id"] for e in hub.query(after=2)] == ["e2", "e3", "e4"]
    assert replay_from("2", hub.query()) == hub.query(after=2)
    # A client with no cursor, or a nonsense one, gets nothing replayed rather
    # than the whole history.
    assert replay_from("", hub.query()) == []


def test_the_sse_frame_carries_the_cursor_a_reconnect_uses() -> None:
    """`id:` is the sequence, not the `event_id` — that is what `Last-Event-ID`
    is compared against on the way back in."""
    hub = a_hub()
    hub.ingest(envelope())

    frame = sse_frame(hub.query()[0]).decode()

    assert frame.startswith("id: 1\n")
    assert "event: ravis.route.selected\n" in frame
    assert json.loads(frame.split("data: ", 1)[1].strip())["event_id"]


# ── Filters (§11.2) ────────────────────────────────────────────────────────


def test_every_filter_narrows_and_none_of_them_invents() -> None:
    hub = a_hub()
    hub.ingest(envelope(event_id="a", severity="error"))
    hub.ingest(envelope(event_id="b", event_type="sirvis.benchmark.finished",
                        source={"service_type": "sirvis"}, trace_id="t2",
                        # Different `data`, so the free-text assertion below is
                        # testing the filter rather than the fixture.
                        data={"suite": "clarvis-agent"}))

    assert [e["event_id"] for e in hub.query(severity="error")] == ["a"]
    assert [e["event_id"] for e in hub.query(service="sirvis")] == ["b"]
    assert [e["event_id"] for e in hub.query(event_type="ravis.route.selected")] == ["a"]
    assert [e["event_id"] for e in hub.query(trace_id="t2")] == ["b"]
    assert [e["event_id"] for e in hub.query(text="qwen3-4b")] == ["a"]
    assert hub.query(service="nobody") == []


# ── Retention (§11.1) ──────────────────────────────────────────────────────


def test_retention_drops_by_age_and_by_count() -> None:
    """Two bounds because they fail differently: age alone lets a burst fill a
    disk inside the window, and a count alone keeps a quiet week forever."""
    hub = a_hub(retention_days=7.0, retention_events=3)
    for n in range(6):
        hub.ingest(envelope(event_id=f"e{n}"))

    removed = hub.enforce_retention()

    assert removed == 3
    assert [e["event_id"] for e in hub.query()] == ["e3", "e4", "e5"]


def test_retention_by_age_uses_arrival_not_the_producer_s_clock() -> None:
    """A producer's clock is its own. Retaining on `occurred_at` would let one
    with a wrong year delete itself on arrival, or never."""
    hub = a_hub(retention_days=7.0, retention_events=10_000)
    hub.ingest(envelope(occurred_at="1999-01-01T00:00:00Z"))

    assert hub.enforce_retention() == 0

    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert hub.enforce_retention(now=later) == 1


def test_quarantine_is_bounded_too() -> None:
    """A producer emitting malformed events emits them at exactly the rate it
    emits good ones."""
    hub = a_hub(retention_events=2)
    for n in range(5):
        hub.ingest({"event_id": f"bad{n}"})

    hub.enforce_retention()

    assert len(hub.quarantined()) == 2


# ── Fan-out (§11.1: bounded buffers) ───────────────────────────────────────


def test_a_subscriber_that_falls_behind_is_dropped_not_waited_for() -> None:
    """§11.1 bounds buffers precisely so a slow subscriber cannot become
    back-pressure on a producer.

    A hub that blocked here would let one stalled browser tab stop the
    ecosystem's telemetry.
    """
    from nervis.events import SUBSCRIBER_BUFFER

    hub = a_hub()
    queue = hub.subscribe()

    for n in range(SUBSCRIBER_BUFFER + 5):
        hub.ingest(envelope(event_id=f"e{n}"))

    # Every event was still stored: the producer was never held up.
    assert len(hub.query(limit=1000)) == SUBSCRIBER_BUFFER + 5
    # And the reader was cut loose with a marker rather than silently starved.
    drained = [queue.get_nowait() for _ in range(queue.qsize())]
    assert drained[-1]["event_type"] == "ecosystem.stream.gap"


def test_nervis_emits_its_own_events_through_the_same_door() -> None:
    """§3.1 has NERVIS implementing *and* consuming the MEP.

    A hub whose own events took a private path would be the one producer nobody
    could validate.
    """
    hub = a_hub()

    hub.emit("nervis.service.state_changed", data={"service": "ravis", "to": "unreachable"})

    stored = hub.query()[0]
    assert stored["source"]["service_type"] == "nervis"
    assert not isinstance(validate(stored), Rejected)


def test_a_live_event_carries_its_cursor() -> None:
    """A frame with an empty `id:` cannot be resumed from.

    The sequence used to be attached only by `_view`, on the way *out* of
    storage, so a broadcast went out without one — and a client that reconnected
    after receiving live events replayed from wherever its last stored read had
    left off, silently duplicating everything in between. Found by watching an
    actual stream.
    """
    hub = a_hub()
    queue = hub.subscribe()

    hub.ingest(envelope())

    live = queue.get_nowait()
    assert live["_sequence"] == 1
    assert sse_frame(live).startswith(b"id: 1\n")


# ── What an adversarial review of the M8 design found in the existing hub ───


def test_quarantine_keeps_the_shape_and_never_the_values() -> None:
    """The validator refuses on envelope shape alone.

    So an otherwise ordinary event that merely omits `occurred_at` had its whole
    `data` stored verbatim — and the quarantine is readable over HTTP. A
    producer that posts a prompt, a file excerpt or a token under the wrong
    event type had it kept and served. Truncating at four thousand characters
    was never the mitigation it looked like: the interesting part of a leaked
    value is rarely past the four-thousandth character.
    """
    hub = a_hub()

    hub.ingest({"event_type": "t", "secret": "sk-live-do-not-store", "path": "/Users/me/x"})

    stored = json.dumps(hub.quarantined())
    assert "sk-live-do-not-store" not in stored
    assert "/Users/me/x" not in stored
    # The diagnostic survives: which keys, and a digest to tell two apart.
    assert "secret" in stored and "path" in stored
    assert "sha256:" in stored


def test_a_fresh_subscriber_starts_near_the_end_not_at_the_beginning() -> None:
    """`_int("") == 0`, and `query(after=0)` returns the *oldest* events.

    So one connection with no `Last-Event-ID` replayed the start of the retained
    history to whoever asked. A subscriber with no cursor is new, not resuming
    from zero: it wants what happens next and a short tail for context.

    Tested as the cursor arithmetic rather than by opening the stream — the
    stream does not end, so asserting on it means racing a generator that is
    designed to outlive the request.
    """
    from nervis.api.events import FRESH_TAIL, _int

    hub = a_hub()
    for n in range(FRESH_TAIL + 30):
        hub.ingest(envelope(event_id=f"e{n}"))

    # What the handler computes for a client that sent no cursor.
    fresh_start = max(0, hub.latest_sequence() - FRESH_TAIL)
    replayed = hub.query(after=fresh_start, limit=FRESH_TAIL)

    assert _int("", 0) == 0                       # the trap
    assert fresh_start > 0                        # and what replaces it
    assert len(replayed) <= FRESH_TAIL
    assert replayed[0]["event_id"] != "e0"


def test_a_feed_with_no_cursor_shows_the_newest_events_not_the_oldest() -> None:
    """"What has happened lately" is the opposite window from replay's.

    `query` scans `ORDER BY sequence` and applies `LIMIT`, which is right for
    replay -- a client resuming from a cursor wants what it missed, in order --
    and exactly wrong for a screen with no cursor, which was silently served the
    N *oldest* events the hub had ever seen. The dashboard's "Recent events"
    card showed the four oldest, and could not display anything from today.
    """
    client = an_api()
    for n in range(30):
        client.post(
            "/api/v1/events",
            json=envelope(event_id=f"01J00000000000000000000{n:03d}", data={"n": n}),
        )

    shown = client.get("/api/v1/events?limit=5").json()["items"]

    assert [item["data"]["n"] for item in shown] == [25, 26, 27, 28, 29], (
        "the five newest, still ascending so no caller learns a second convention"
    )


def test_replay_from_a_cursor_still_starts_at_the_cursor() -> None:
    """The other half of the same change: `after` means replay, and replay is
    oldest-first from that point. Asserted beside the test above because moving
    the no-cursor case to the newest window is only safe if this stays put."""
    client = an_api()
    for n in range(30):
        client.post(
            "/api/v1/events",
            json=envelope(event_id=f"01J00000000000000000000{n:03d}", data={"n": n}),
        )

    replayed = client.get("/api/v1/events?after=3&limit=4").json()["items"]

    assert [item["data"]["n"] for item in replayed] == [3, 4, 5, 6], (
        "what the client missed, in the order it happened"
    )


def test_a_subscriber_that_falls_behind_has_its_stream_closed() -> None:
    """§4.1's gap frame has to end the connection, or it tells the client nothing.

    `_broadcast` drops a subscriber whose queue is full, then pushes a gap
    marker into the now-orphaned queue. Nothing after that will ever arrive on
    it -- but the SSE generator went on looping, yielding a heartbeat every
    twelve seconds forever. The client saw a healthy connection carrying no
    events, and because `EventSource` only reconnects on error or close it never
    reconnected. The gap frame said "subscriber fell behind and was
    disconnected" while the socket stayed open, so the one frame whose job is to
    make a client notice was the frame that hid the problem.

    Driven through the real endpoint's async generator rather than over HTTP:
    the behaviour under test is what the generator does after that frame, and a
    `TestClient` stream is pull-based, so not reading is exactly what will not
    reproduce it.
    """
    import asyncio

    from nervis.api.events import stream
    from nervis.events import SUBSCRIBER_BUFFER

    async def exercise() -> list[bytes]:
        hub = a_hub()

        class _Request:
            app = type("_App", (), {"state": type("_S", (), {"hub": hub})()})()
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        response = stream(_Request())  # type: ignore[arg-type]
        frames = (await response).body_iterator

        # Drain until the live marker, so the generator is parked on the queue
        # exactly as a connected client leaves it.
        #
        # Read to a *landmark* rather than by count. This consumed exactly one
        # frame, which was right until the stream began advertising `retry:
        # 3000` ahead of the backlog — then one frame left the generator still
        # in its replay loop rather than waiting on the queue, and the failure
        # surfaced as "the stream never closed after the gap frame", which
        # describes neither the change nor the cause.
        while b"ecosystem.stream.live" not in await frames.__anext__():
            pass

        # More than the buffer holds, with nothing consuming: the hub drops this
        # subscriber and writes the gap.
        for n in range(SUBSCRIBER_BUFFER + 10):
            hub.ingest(envelope(event_id=f"01J0000000000000000000{n:04d}"))

        seen: list[bytes] = []
        for _ in range(SUBSCRIBER_BUFFER + 20):
            try:
                seen.append(await asyncio.wait_for(frames.__anext__(), timeout=2))
            except StopAsyncIteration:
                return seen
        raise AssertionError("the stream never closed after the gap frame")

    frames = asyncio.run(exercise())

    joined = b"".join(frames)
    assert b"ecosystem.stream.gap" in joined, "the client is told why it was cut off"
    assert not frames[-1].startswith(b":"), (
        "the last thing sent is the gap, not another heartbeat"
    )


# ── §4.1's stream contract, the three clauses that were prose ───────────────


def test_a_gap_frame_does_not_erase_the_client_s_cursor() -> None:
    """The frame whose job is to make a client catch up cannot delete its place.

    `sse_frame` wrote `id: ` with an empty value for any event without a
    `_sequence`, and the gap frame is the one event that deliberately has none.
    Per the SSE specification an `id` field sets the last-event-ID buffer to any
    value without a NUL — the empty string included — so the gap frame cleared
    the cursor on its way past. A gap frame is terminal, so the client
    reconnected at once, with no `Last-Event-ID`, was treated as new, and
    received a short tail instead of the replay it was owed.

    `_broadcast`'s docstring asserted the opposite behaviour as its rationale.
    """
    gap = {"event_type": "ecosystem.stream.gap", "data": {"reason": "fell behind"}}

    frame = sse_frame(gap)

    assert not frame.startswith(b"id:"), "a frame with no sequence must carry no id"
    assert b"\nid:" not in frame
    assert frame.startswith(b"event: ecosystem.stream.gap\n")
    # And the ordinary case still carries one, or nothing can resume at all.
    assert sse_frame({"event_type": "x", "_sequence": 7}).startswith(b"id: 7\n")


def test_the_stream_advertises_a_reconnect_interval() -> None:
    """Runbook §4.1: "Reconnect advertises `retry: 3000`."

    Without it every client picks its own default and they differ by browser,
    which turns a restart into a thundering herd or a three-second stall
    depending on who is looking.

    Driven through the generator rather than a `TestClient` stream, for the
    reason the falls-behind test above gives: this endpoint never ends, so a
    pull-based client hangs on the way out rather than on the way in.
    """
    import asyncio

    from nervis.api.events import stream

    async def first_frame() -> bytes:
        hub = a_hub()

        class _Request:
            app = type("_App", (), {"state": type("_S", (), {"hub": hub})()})()
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        response = await stream(_Request())  # type: ignore[arg-type]
        frames = response.body_iterator
        try:
            return await frames.__anext__()
        finally:
            await frames.aclose()

    assert asyncio.run(first_frame()) == b"retry: 3000\n\n"


def test_a_cursor_older_than_retention_is_refused_rather_than_silently_moved() -> None:
    """Runbook §4.1: "A cursor outside retention returns 409 EVENT_CURSOR_EXPIRED."

    Resuming such a client from whatever survived hands it a stream with a hole
    in it and no way to know — the same silence the gap frame exists to break,
    arriving through the front door.
    """
    api = an_api()
    hub = api.app.state.hub
    for n in range(4):
        hub.emit("nervis.test", data={"n": n})
    # Delete the beginning, so the floor is above the cursor the client holds.
    with hub._database.connection as connection:
        connection.execute("DELETE FROM event WHERE sequence <= 2")

    response = api.get("/api/v1/events/stream", headers={"last-event-id": "1"})

    assert response.status_code == 409
    # §16 item 10: an HTTPException carries the §4.5 envelope now, so the
    # refusal reads off `error` like every other refusal in the ecosystem.
    body = response.json()["error"]
    assert body["code"] == "EVENT_CURSOR_EXPIRED"
    # The retention floor moved into `details`, which is where §4.5 puts
    # everything a refusal carries beyond its code and message.
    assert body["details"]["oldest_sequence"] == 3


def test_a_cursor_that_is_not_a_number_is_refused() -> None:
    """The bad-cursor half of a bug whose good half was already fixed.

    `_int("abc", 0)` is 0 and the header was present, so the stream replayed the
    *oldest* retained events to a client that had asked for something
    unreadable. The comment above the code records that exact fix for the
    no-cursor case; it never covered this one.
    """
    api = an_api()

    response = api.get("/api/v1/events/stream", headers={"last-event-id": "not-a-number"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EVENT_CURSOR_INVALID"


def test_a_cursor_inside_retention_still_resumes() -> None:
    """The control. A check that refused every cursor would pass the two above."""
    import asyncio

    from nervis.api.events import stream

    async def replayed() -> bytes:
        hub = a_hub()
        for n in range(3):
            hub.emit("nervis.test", data={"n": n})

        class _Request:
            app = type("_App", (), {"state": type("_S", (), {"hub": hub})()})()
            headers = {"last-event-id": "1"}
            query_params: dict[str, str] = {}

        response = await stream(_Request())  # type: ignore[arg-type]
        frames = response.body_iterator
        seen = b""
        try:
            while b"ecosystem.stream.live" not in seen:
                seen += await frames.__anext__()
        finally:
            await frames.aclose()
        return seen

    joined = asyncio.run(replayed())

    assert b"id: 2" in joined and b"id: 3" in joined
    assert b"id: 1\n" not in joined, "the cursor is exclusive"


def test_an_open_stream_ends_when_the_service_is_stopping() -> None:
    """A held-open stream must not hold the service open with it.

    `frames()` never returns on its own and uvicorn's graceful shutdown waits
    for open connections, so one dashboard tab with the feed open held NERVIS in
    "Waiting for connections to close" indefinitely — port released, process
    alive, indistinguishable from a service that is down and refusing to die.

    Found by restarting NERVIS with a dashboard open, which is the ordinary case
    and only became possible once Stage 7 gave the page a real stream to hold.
    Closing costs the client nothing: it is told `retry: 3000` and reconnects.
    """
    import asyncio

    from nervis.api.events import stream

    async def exercise() -> bool:
        hub = a_hub()
        stopping = asyncio.Event()

        class _Request:
            app = type("_App", (), {"state": type(
                "_S", (), {"hub": hub, "stopping": stopping})()})()
            headers: dict[str, str] = {}
            query_params: dict[str, str] = {}

        response = await stream(_Request())  # type: ignore[arg-type]
        frames = response.body_iterator
        while b"ecosystem.stream.live" not in await frames.__anext__():
            pass

        # The service begins shutting down while the client is still attached.
        stopping.set()
        hub.emit("nervis.test", data={})
        try:
            await asyncio.wait_for(frames.__anext__(), timeout=2)
        except StopAsyncIteration:
            return True
        except TimeoutError:
            return False
        return False

    assert asyncio.run(exercise()), "the stream did not end when the service stopped"
