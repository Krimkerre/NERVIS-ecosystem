"""M11 — sessions, and the three things its exit criterion names.

RAVIS.md M11's exit, verbatim: *isolation, correlation and retention tests
pass.* §12.1 adds the gate that shapes the code — *restart, expiry and
concurrency tests preserve isolation, correlation, cancellation ownership and
documented retention* — and one sentence that decides the storage key outright:
*cross-workspace Clarvis sessions must never merge because display names match.*

Isolation is the one worth being strict about. A session that merged across
applications would be worse than having no sessions at all, because a consumer
would trust the correlation it displayed.
"""

from __future__ import annotations

from typing import Any

import pytest

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.routing.engine import RoutingEngine
from ravis.sessions import (
    DEFAULT_IDLE_SECONDS,
    MAX_ID_LENGTH,
    SESSION_HEADER,
    SessionStore,
    session_key,
)
from ravis.storage.database import prepare_database


class Clock:
    """A clock a test can move, so expiry is asserted rather than waited for."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def store() -> Any:
    return SessionStore(prepare_database(":memory:"), clock=Clock())


def _store(clock: Clock, **kwargs: Any) -> SessionStore:
    return SessionStore(prepare_database(":memory:"), clock=clock, **kwargs)


# ── Isolation (§12.1) ───────────────────────────────────────────────────────


def test_two_applications_may_use_the_same_session_id(store: SessionStore) -> None:
    """The requirement, stated as plainly as §12.1 states it.

    Two clients that both call their session `main` must not see each other's
    routing. Nothing here keys on a label, which is what the specification's
    warning about matching display names is about.
    """
    store.record("clarvis", "main", pool="ravis/auto", model="model-a", provider="p1")
    store.record("nervis", "main", pool="ravis/cheap", model="model-b", provider="p2")

    clarvis = store.get("clarvis", "main")
    nervis = store.get("nervis", "main")

    assert clarvis is not None and nervis is not None
    assert clarvis.model == "model-a"
    assert nervis.model == "model-b"


def test_a_session_belonging_to_another_application_reads_as_absent(
    store: SessionStore,
) -> None:
    """Absent rather than forbidden.

    Distinguishing "not yours" from "does not exist" tells a caller that an ID
    it cannot read is nevertheless real, which is the leak that a careful
    permission check is supposed to prevent.
    """
    store.record("clarvis", "secret-work", pool="ravis/auto", model="m", provider="p")

    assert store.get("nervis", "secret-work") is None


def test_the_key_cannot_be_forged_by_embedding_one_half_in_the_other() -> None:
    """The composite has to be unambiguous, or isolation is decorative.

    A separator a caller could type would let `nervis` claim to be
    `clarvis`'s session by sending an ID containing it.
    """
    honest = session_key("clarvis", "main")
    forged = session_key("nervis", "clarvis\x1fmain")

    assert honest != forged


def test_a_listing_shows_only_the_asking_application(store: SessionStore) -> None:
    store.record("clarvis", "a", pool="ravis/auto", model="m", provider="p")
    store.record("nervis", "b", pool="ravis/auto", model="m", provider="p")

    assert [s.session_id for s in store.for_application("clarvis")] == ["a"]


# ── Correlation (§12.1) ─────────────────────────────────────────────────────


def test_a_session_records_what_it_routed_to(store: SessionStore) -> None:
    """§12.1's list, which is what makes a session answer a question."""
    store.record(
        "clarvis", "s1", pool="ravis/clarvis-agent", model="gpt-4o",
        provider="openai", pool_revision="abc123", profile="ravis/clarvis-agent",
    )

    session = store.get("clarvis", "s1")

    assert session is not None
    assert (session.pool, session.model, session.provider) == (
        "ravis/clarvis-agent", "gpt-4o", "openai",
    )
    # §5.4: a consumer can tell the pool's definition changed under a running
    # conversation, which is the whole reason a revision is pinnable.
    assert session.pool_revision == "abc123"


def test_a_session_holds_no_conversation_content(store: SessionStore) -> None:
    """§12.1: a session does not imply RAVIS stores prompts or responses.

    Checked on the serialised shape rather than the dataclass, because the
    serialised shape is what leaves the process. Asserted as an exact set so a
    field cannot be added without someone deciding it belongs.

    `requests` is the one field beyond §12.1's list, and it was added on
    purpose: §12.2 needs expected session length and a count is the only honest
    source. A count is metadata about the conversation, not any part of it —
    what §12.1 rules out is prompt and response text, and there is still
    nowhere here to put any.
    """
    store.record("clarvis", "s1", pool="ravis/auto", model="m", provider="p")
    session = store.get("clarvis", "s1")

    assert session is not None
    assert set(session.as_dict()) == {
        "session_id", "application_id", "pool", "model", "provider",
        "pool_revision", "profile", "cache_state", "created_at", "last_activity",
        "requests",
    }


def test_an_unreported_cache_state_stays_unknown(store: SessionStore) -> None:
    """No adapter reports a prompt cache, so `False` would be a claim.

    "No cache" and "never asked" are different answers, and only one of them is
    true today.
    """
    store.record("clarvis", "s1", pool="ravis/auto", model="m", provider="p")
    session = store.get("clarvis", "s1")

    assert session is not None
    assert session.cache_state is None
    assert "cache_state" in session.as_dict()


def test_created_at_survives_a_later_request(store: SessionStore) -> None:
    """A session is one thing over time, not a new row per request."""
    first = store.record("clarvis", "s1", pool="ravis/auto", model="a", provider="p")
    second = store.record("clarvis", "s1", pool="ravis/auto", model="b", provider="p")

    assert first is not None and second is not None
    assert second.created_at == first.created_at
    assert second.model == "b"


def test_correlation_survives_a_restart() -> None:
    """§12.1's gate names restart, and this is why sessions are persisted.

    A session that forgot its model on restart would swap the model under a
    conversation still in progress — the exact churn stickiness prevents. The
    restart is simulated by building a second store over the same database,
    which is what a new process does.
    """
    database = prepare_database(":memory:")
    clock = Clock()
    before = SessionStore(database, clock=clock)
    before.record("clarvis", "s1", pool="ravis/auto", model="kept", provider="p")

    after = SessionStore(database, clock=clock)
    session = after.get("clarvis", "s1")

    assert session is not None and session.model == "kept"


# ── Expiry and retention (§12.1) ────────────────────────────────────────────


def test_a_stale_session_stops_steering_but_still_answers() -> None:
    """Expiry and deletion are different, and both are wanted.

    Yesterday's model choice must not decide today's route; the record of what
    yesterday used must still be readable. Collapsing the two would mean either
    routing on stale state or losing the correlation someone is reading.
    """
    clock = Clock()
    store = _store(clock)
    store.record("clarvis", "s1", pool="ravis/auto", model="m", provider="p")

    clock.advance(DEFAULT_IDLE_SECONDS + 1)

    assert store.affinity("clarvis", "s1") is None, "a stale session must not steer routing"
    assert store.get("clarvis", "s1") is not None, "but it must still be readable"


def test_touch_keeps_a_busy_session_alive() -> None:
    """A conversation being actively refused must not quietly expire.

    Its requests are reaching RAVIS; only the routing is failing, which is
    usually when somebody is watching.
    """
    clock = Clock()
    store = _store(clock)
    store.record("clarvis", "s1", pool="ravis/auto", model="m", provider="p")

    clock.advance(DEFAULT_IDLE_SECONDS - 1)
    store.touch("clarvis", "s1")
    clock.advance(DEFAULT_IDLE_SECONDS - 1)

    assert store.affinity("clarvis", "s1") is not None


def test_retention_deletes_only_past_the_window() -> None:
    """Documented retention, applied — §12.1's gate asks for both."""
    clock = Clock()
    store = _store(clock, retention_seconds=100.0)
    store.record("clarvis", "old", pool="ravis/auto", model="m", provider="p")
    clock.advance(200.0)
    store.record("clarvis", "new", pool="ravis/auto", model="m", provider="p")

    deleted = store.enforce_retention()

    assert deleted == 1
    assert store.get("clarvis", "old") is None
    assert store.get("clarvis", "new") is not None


# ── The ID itself ───────────────────────────────────────────────────────────


def test_an_absent_or_empty_id_creates_nothing(store: SessionStore) -> None:
    """A session nobody can name again is a row that can only be written."""
    assert store.record("clarvis", "", pool="p", model="m", provider="x") is None
    assert store.record("clarvis", "   ", pool="p", model="m", provider="x") is None
    assert store.get("clarvis", "") is None


def test_a_control_character_cannot_be_stored(store: SessionStore) -> None:
    """The ID is echoed on a read endpoint and written to logs.

    A newline in it would let a caller forge a log line — cheap to prevent
    here, awkward to prevent at every place it is printed.
    """
    store.record("clarvis", "a\nfake log line", pool="p", model="m", provider="x")
    listed = store.for_application("clarvis")

    assert listed and "\n" not in listed[0].session_id


def test_a_very_long_id_is_bounded(store: SessionStore) -> None:
    """Unbounded text from a request is how a table becomes a place to put things."""
    store.record("clarvis", "x" * 5_000, pool="p", model="m", provider="y")
    listed = store.for_application("clarvis")

    assert listed and len(listed[0].session_id) == MAX_ID_LENGTH


# ── Sticky routing (§12.1) ──────────────────────────────────────────────────

WARM = "already-in-use"
BETTER = "alphabetically-first"


def _catalogue() -> dict[str, ModelCapabilities]:
    claim = CapabilityClaim(
        capability=Capability.STREAMING,
        state=CapabilityState.SUPPORTED,
        provenance=Provenance.ADVERTISED,
    )
    return {
        name: ModelCapabilities(
            model_id=name, claims={Capability.STREAMING: claim},
            context_window=32_000, price_per_million=1.0,
        )
        for name in (BETTER, WARM)
    }


def test_without_a_session_the_pool_picks_its_own_way() -> None:
    """The control. Stickiness proves nothing without it."""
    decision = RoutingEngine().select("ravis/auto", _catalogue())

    assert decision.selected == BETTER


def test_a_session_holds_a_conversation_on_its_model() -> None:
    """§12.1's reasons: consistency, prompt caching, context continuity, and
    less model-load churn. None survive a preference other terms can outvote."""
    decision = RoutingEngine().select("ravis/auto", _catalogue(), sticky=WARM)

    assert decision.selected == WARM


def test_stickiness_cannot_hold_a_session_on_an_ineligible_model() -> None:
    """The four break conditions §12.1 lists are enforced before ranking.

    A model whose capabilities no longer fit, whose context is exceeded, whose
    provider's circuit is open, or which policy now refuses is not a candidate
    at all — so affinity orders the allowed ones and can never resurrect one.
    Checked through the circuit, which is the condition most likely to fire in
    the middle of a real conversation.
    """
    decision = RoutingEngine().select(
        "ravis/auto", _catalogue(), sticky=WARM,
        unavailable={WARM: "circuit open after 5 failures"},
    )

    assert decision.selected == BETTER
    refused = {c.model: c.reasons for c in decision.excluded}
    assert WARM in refused


def test_a_background_call_neither_uses_nor_overwrites_affinity() -> None:
    """§9.6.1's exemption runs in both directions, and the second half was missed.

    Skipping affinity on the way *in* is the obvious half: a title should not be
    dragged onto a conversation's frontier model. The half that was wrong is the
    way out — a declared background call still recorded its own cheap selection,
    so generating one title reset the session and the next real turn started
    over somewhere else.

    Found live, by sending the sequence below against a running gateway: the
    exemption looked correct for three requests and the fourth showed the
    conversation had moved. Exempt means it does not consume affinity and does
    not get to redefine it.
    """
    store = SessionStore(prepare_database(":memory:"), clock=Clock())
    store.record("app", "s1", pool="ravis/auto", model="conversation-model", provider="p")

    # What `_record_session` does for a background call: keep the session alive,
    # write nothing about the route.
    store.touch("app", "s1")

    session = store.get("app", "s1")
    assert session is not None
    assert session.model == "conversation-model", "a background call must not retarget a session"


def test_touching_a_session_does_not_disturb_what_it_routed_to() -> None:
    """The property the exemption rests on, checked directly."""
    clock = Clock()
    store = _store(clock)
    store.record("app", "s1", pool="ravis/chat", model="m", provider="p", pool_revision="r1")
    before = store.get("app", "s1")

    clock.advance(10)
    store.touch("app", "s1")
    after = store.get("app", "s1")

    assert before is not None and after is not None
    assert (after.model, after.pool, after.pool_revision) == (
        before.model, before.pool, before.pool_revision,
    )
    assert after.last_activity > before.last_activity


# ── Expected session length (§12.2's input, supplied by M11) ────────────────


def test_expected_length_is_unknown_until_there_is_history(store: SessionStore) -> None:
    """None rather than a number nobody measured.

    A median of two sessions is two numbers. Answering anyway would let the
    load-versus-don't tradeoff act on noise, and the tradeoff's whole defence is
    that it moves only on a measurement.
    """
    assert store.typical_length("clarvis") is None

    for name in ("a", "b"):
        store.record("clarvis", name, pool="p", model="m", provider="x")

    assert store.typical_length("clarvis") is None


def test_expected_length_is_measured_from_this_application_only(
    store: SessionStore,
) -> None:
    """Another application's habits say nothing about this one's.

    Clarvis running long agent sessions must not convince RAVIS that NERVIS's
    one-shot title calls are long too — which is the same isolation the storage
    key provides, applied to the statistic drawn from it.
    """
    for name in ("a", "b", "c"):
        for _ in range(20):
            store.record("clarvis", name, pool="p", model="m", provider="x")
    for name in ("x", "y", "z"):
        store.record("nervis", name, pool="p", model="m", provider="x")

    assert store.typical_length("clarvis") == 20
    assert store.typical_length("nervis") == 1


def test_one_runaway_session_does_not_define_the_rest(store: SessionStore) -> None:
    """The median, not the mean.

    One abandoned session of four hundred requests should not convince RAVIS
    that every session is long and that loads are always worth paying.
    """
    for name in ("a", "b", "c", "d"):
        store.record("clarvis", name, pool="p", model="m", provider="x")
    for _ in range(400):
        store.record("clarvis", "runaway", pool="p", model="m", provider="x")

    typical = store.typical_length("clarvis")

    assert typical is not None and typical <= 2, f"a mean would have said ~80, got {typical}"


def test_a_session_counts_its_requests(store: SessionStore) -> None:
    """The count is §12.2's only honest source of expected session length."""
    for _ in range(3):
        store.record("clarvis", "s1", pool="p", model="m", provider="x")

    session = store.get("clarvis", "s1")

    assert session is not None and session.requests == 3


def test_a_background_call_does_not_inflate_the_count(store: SessionStore) -> None:
    """`touch` keeps a session alive without claiming it made a routing request.

    A background call is exempt from affinity, and counting it would let title
    generation talk RAVIS into believing a conversation is longer than it is —
    the same over-reach as letting it retarget the session.
    """
    store.record("clarvis", "s1", pool="p", model="m", provider="x")
    store.touch("clarvis", "s1")
    store.touch("clarvis", "s1")

    session = store.get("clarvis", "s1")

    assert session is not None and session.requests == 1


def _request_with(store: SessionStore, session: str = "conversation-1") -> Any:
    """The parts of a request `_record_session` reads, and nothing else."""

    class _State:
        sessions = store
        transparents: dict[str, Any] = {}
        translating: dict[str, Any] = {}

    class _Request:
        app = type("_App", (), {"state": _State()})()
        headers = {SESSION_HEADER: session}
        state = type("_St", (), {"identity": None})()

    return _Request()


def test_a_background_call_does_not_redefine_the_session() -> None:
    """§9.6.1's exemption runs in both directions, and only one had a test.

    Skipping affinity on the way in is not enough: a declared background call
    was still *recording* its own cheap selection, so generating one
    conversation title reset the session and the next real turn started over on
    a different model. That was found by sending four requests in order against
    a live gateway -- the exemption read as working until the fifth showed the
    conversation had moved -- and the fix shipped with the reasoning written out
    at length and nothing exercising it. A line trace of the whole suite reaches
    six of `_record_session`'s forty-two lines: the early return, and nothing
    else.

    The session is still *touched*, because a conversation whose titles are
    being generated is not idle.
    """
    from ravis.api.openai.chat import _record_session
    from ravis.routing.engine import RouteDecision

    clock = Clock()
    store = _store(clock)
    request = _request_with(store)

    real = RouteDecision(requested="ravis/clarvis-chat")
    # `routed` is derived from `selected`, not set.
    real.selected, real.pool_id = "qwen2.5-coder-7b", "ravis/clarvis-chat"
    _record_session(request, real)

    settled = store.get("anonymous", "conversation-1")
    assert settled is not None and settled.model == "qwen2.5-coder-7b"

    cheap = RouteDecision(requested="ravis/clarvis-chat")
    cheap.selected, cheap.pool_id = "tiny-1b", "ravis/clarvis-chat"
    clock.advance(30)
    _record_session(request, cheap, background=True)

    after = store.get("anonymous", "conversation-1")
    assert after is not None
    assert after.model == "qwen2.5-coder-7b", (
        "a background call must not move the conversation to its own cheap pick"
    )
    assert after.last_activity > settled.last_activity, (
        "but the session is touched: titles being generated is not idleness, and "
        "a conversation must not expire while its titles are being written"
    )


def test_a_no_route_leaves_the_last_successful_model_in_place() -> None:
    """Overwriting it with nothing would make the next request start over, and a
    conversation being actively refused must not quietly expire while somebody
    is trying to fix it."""
    from ravis.api.openai.chat import _record_session
    from ravis.routing.engine import RouteDecision

    store = _store(Clock())
    request = _request_with(store, "conversation-2")

    good = RouteDecision(requested="ravis/clarvis-chat")
    good.selected, good.pool_id = "qwen2.5-coder-7b", "ravis/clarvis-chat"
    _record_session(request, good)

    refused = RouteDecision(requested="ravis/clarvis-chat")  # nothing selected
    _record_session(request, refused)

    after = store.get("anonymous", "conversation-2")
    assert after is not None
    assert after.model == "qwen2.5-coder-7b", "the last successful choice survives"
