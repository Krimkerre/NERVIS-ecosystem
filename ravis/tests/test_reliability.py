"""Health, failure classification, circuit breakers and the retry budget (§10).

These are unit-level: no HTTP, no application, no fixture upstream. The point of
that separation is that §10's *rules* can be asserted directly. "Do not route
around a safety refusal" is a property of a policy table, and a test that had to
stand up a proxy to check it would be testing the proxy.

The end-to-end behaviour those rules produce is in `test_fallback.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ravis.reliability import (
    AttemptChain,
    BreakerState,
    FailureClass,
    HealthRegistry,
    HealthScope,
    RetryBudget,
    classify_error_body,
    classify_exception,
    classify_response,
)


class FakeClock:
    """A clock the test moves by hand.

    Every breaker transition is a function of elapsed time, so the alternative
    is a suite that sleeps through cooldowns — slow, and flaky the moment a
    machine is loaded.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── Classification (§10's eleven classes) ────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, b"{}", FailureClass.AUTHENTICATION),
        (403, b"{}", FailureClass.AUTHENTICATION),
        (404, b"{}", FailureClass.MODEL_UNAVAILABLE),
        (429, b"{}", FailureClass.RATE_LIMIT),
        (503, b"{}", FailureClass.OVERLOAD),
        (504, b"{}", FailureClass.TIMEOUT),
        (400, b"{}", FailureClass.INVALID_REQUEST),
        (500, b"{}", FailureClass.UNKNOWN),
        (400, b'{"error":{"message":"maximum context length is 4096"}}',
         FailureClass.CONTEXT_OVERFLOW),
        (400, b'{"error":{"code":"content_filter"}}', FailureClass.CONTENT_REFUSAL),
        (400, b'{"error":{"message":"this model does not support tools"}}',
         FailureClass.TOOL_INCOMPATIBILITY),
        # OpenRouter's refusal arrives as a 404; the body must win over the status.
        (404, b'{"error":{"message":"No endpoints found that support tool use."}}',
         FailureClass.TOOL_INCOMPATIBILITY),
        (500, b'{"error":{"message":"failed to allocate 8 GB"}}', FailureClass.LOCAL_OOM),
    ],
)
def test_upstream_errors_are_classified(status: int, body: bytes,
                                        expected: FailureClass) -> None:
    """Each of §10's classes is reachable from something a real upstream sends."""
    assert classify_response(status, body) is expected


def test_a_successful_response_is_not_a_failure() -> None:
    """None means "did not fail", which is an answer rather than a sentinel.

    Runbook §14.4: absence carries meaning here. A classifier that returned
    `UNKNOWN` for a 200 would make every success look like an unclassified
    error to anything counting them.
    """
    assert classify_response(200, b'{"choices":[]}') is None


def test_a_connect_timeout_is_a_timeout_not_a_connection_failure() -> None:
    """The distinction decides whether the same target is retried.

    `httpx.ConnectTimeout` is both, and reading it as a connection failure would
    retry a target that is merely slow — paying the full timeout twice before
    trying anything else.
    """
    assert classify_exception(httpx.ConnectTimeout("slow")) is FailureClass.TIMEOUT
    assert classify_exception(httpx.ConnectError("refused")) is FailureClass.CONNECTION


def test_an_error_body_is_classified_without_a_status_to_help() -> None:
    """`classify_response` short-circuits below 400, which is blind to the case
    that matters most on a local runtime: a 200 carrying an error object."""
    assert classify_error_body(b'{"error":"model not found"}') is FailureClass.MODEL_UNAVAILABLE
    assert classify_error_body(b'{"error":"out of memory"}') is FailureClass.LOCAL_OOM


def test_an_unrecognised_error_body_fails_closed() -> None:
    """The first version defaulted to a fallback-eligible class, and that was
    wrong: the *same* refusal would then produce opposite decisions depending on
    the status the upstream happened to attach, with the permissive branch being
    the 2xx one. §10's "do not route around a safety refusal" carries no status
    qualifier, and no marker table is exhaustive over every provider's wording.

    Failing closed is never a regression either — before the gate existed, a 200
    carrying an error was forwarded as a successful answer."""
    assert classify_error_body(b'{"error":"something new"}') is FailureClass.UNKNOWN
    assert FailureClass.UNKNOWN.policy.may_fall_back is False


def test_the_runtimes_own_wording_for_an_unloaded_model_is_recognised() -> None:
    """Which is what makes failing closed affordable. "Model unloaded or
    unavailable" is what the configured LM Studio answers with — inside a 200 —
    and it matched none of the original three markers."""
    assert classify_error_body(
        b'{"error":"Model unloaded or unavailable"}'
    ) is FailureClass.MODEL_UNAVAILABLE
    assert FailureClass.MODEL_UNAVAILABLE.policy.may_fall_back is True


def test_a_safety_refusal_inside_a_success_status_is_still_never_routed_around() -> None:
    """The markers were three OpenAI/Azure `code` spellings. Azure's own prose,
    Gemini's "blocked due to SAFETY" and any plain-language wording all missed
    them — and a refusal that is not recognised is one that gets routed around."""
    for wording in (
        b'{"error":"blocked due to SAFETY"}',
        b'{"error":"the request violates our content filtering policy"}',
    ):
        assert classify_error_body(wording) is FailureClass.CONTENT_REFUSAL


def test_a_success_that_delivered_nothing_is_the_models_problem_not_the_providers() -> None:
    """Scoped to the model so the pool's next candidate — on the same upstream —
    is still reachable. A provider-scoped circuit would take every model out
    because one of them was unloaded."""
    policy = FailureClass.INVALID_UPSTREAM_RESPONSE.policy

    assert policy.scope is HealthScope.MODEL
    assert policy.retry_same_target is False


def test_a_model_scoped_failure_releases_the_providers_half_open_probe() -> None:
    """The latch, and it is terminal for the process when it happens.

    An attempt is claimed on *both* records, so both may be left `probing`, and
    a failure blames only one of them. The unblamed one stays probing forever:
    `allows()` is False while probing, only a success clears it, and no success
    can arrive while `allows()` is False. Reached most easily on the recovery
    path — a half-open probe is by definition the first request to a provider
    that was just down, which is exactly the moment a runtime answers "model
    unloaded" while it comes back up.
    """
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=60.0, clock=clock)
    health.record(FailureClass.CONNECTION, "model-a", "upstream", clock())
    clock.advance(61.0)
    chain = _chain(clock, health)
    chain.load("model-a", [])
    started = chain.begin("model-a")

    chain.failed("model-a", started, FailureClass.MODEL_UNAVAILABLE)

    assert health.allows(HealthScope.PROVIDER, "upstream") is True


def test_a_safety_refusal_is_never_routed_around() -> None:
    """§10, verbatim: do not route around a safety refusal.

    Asserted against the policy rather than through a request, because this is
    the rule itself. If someone later decides a refusal should fall back, this
    test is what makes them say so out loud.
    """
    assert FailureClass.CONTENT_REFUSAL.policy.may_fall_back is False
    assert FailureClass.CONTENT_REFUSAL.policy.retry_same_target is False


def test_only_a_provably_undelivered_request_is_retried_on_the_same_target() -> None:
    """A retry must not be able to duplicate work the upstream already started."""
    retryable = [
        failure for failure in FailureClass if failure.policy.retry_same_target
    ]

    assert retryable == [FailureClass.CONNECTION]


def test_a_bad_request_is_nobody_provider_health_problem() -> None:
    """A client's mistake must not count against a provider (§10, runbook §14.4)."""
    assert FailureClass.INVALID_REQUEST.policy.scope is HealthScope.NONE
    assert FailureClass.CONTEXT_OVERFLOW.policy.scope is HealthScope.NONE


# ── Circuit breaker ──────────────────────────────────────────────────────────


def test_a_circuit_opens_only_after_the_threshold() -> None:
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=3, cooldown_seconds=30.0, clock=clock)
    target = health.of(HealthScope.PROVIDER, "upstream")

    for _ in range(2):
        target.failed(FailureClass.CONNECTION, target.begin())
    assert target.state is BreakerState.CLOSED

    target.failed(FailureClass.CONNECTION, target.begin())
    assert target.state is BreakerState.OPEN
    assert target.allows() is False


def test_one_success_closes_a_circuit_that_was_nearly_open() -> None:
    """Consecutive, not cumulative: a provider that recovered is healthy."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=3, cooldown_seconds=30.0, clock=clock)
    target = health.of(HealthScope.PROVIDER, "upstream")

    target.failed(FailureClass.CONNECTION, target.begin())
    target.failed(FailureClass.CONNECTION, target.begin())
    target.succeeded(target.begin())
    target.failed(FailureClass.CONNECTION, target.begin())
    target.failed(FailureClass.CONNECTION, target.begin())

    assert target.state is BreakerState.CLOSED


def test_an_open_circuit_becomes_half_open_after_the_cooldown() -> None:
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=30.0, clock=clock)
    target = health.of(HealthScope.PROVIDER, "upstream")
    target.failed(FailureClass.CONNECTION, target.begin())

    clock.advance(29.0)
    assert target.state is BreakerState.OPEN

    clock.advance(2.0)
    assert target.state is BreakerState.HALF_OPEN
    assert target.allows() is True


def test_half_open_admits_exactly_one_probe() -> None:
    """A burst of requests must not all be sent at a target presumed broken."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=10.0, clock=clock)
    target = health.of(HealthScope.PROVIDER, "upstream")
    target.failed(FailureClass.CONNECTION, target.begin())
    clock.advance(11.0)

    assert target.allows() is True
    target.begin()
    assert target.allows() is False


def test_a_failed_probe_reopens_the_circuit_immediately() -> None:
    """The probe asked one question. One answer is enough."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=3, cooldown_seconds=10.0, clock=clock)
    target = health.of(HealthScope.PROVIDER, "upstream")
    for _ in range(3):
        target.failed(FailureClass.CONNECTION, target.begin())
    clock.advance(11.0)

    started = target.begin()
    target.failed(FailureClass.CONNECTION, started)

    assert target.state is BreakerState.OPEN


def test_a_client_error_never_opens_a_providers_circuit() -> None:
    """Otherwise one client sending bad JSON takes the provider out of service."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=2, cooldown_seconds=10.0, clock=clock)

    for _ in range(5):
        health.record(FailureClass.INVALID_REQUEST, "model-a", "upstream", clock())

    assert health.of(HealthScope.PROVIDER, "upstream").state is BreakerState.CLOSED
    assert health.of(HealthScope.MODEL, "model-a").state is BreakerState.CLOSED
    # It is still counted. Not opening a circuit is not the same as not noticing.
    assert health.of(HealthScope.MODEL, "model-a").failures_by_class == {"invalid_request": 5}


def test_a_provider_circuit_takes_every_model_behind_it_out_at_once() -> None:
    """One refused connection should not be rediscovered thirteen times."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=10.0, clock=clock)
    health.record(FailureClass.CONNECTION, "model-a", "upstream", clock())

    unavailable = health.unavailable(["model-a", "model-b"], "upstream")

    assert set(unavailable) == {"model-a", "model-b"}
    assert "circuit open" in unavailable["model-a"]


def test_a_model_circuit_takes_out_only_that_model() -> None:
    """An OOM says nothing about the model beside it (§10's failure scoping)."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=10.0, clock=clock)
    health.record(FailureClass.LOCAL_OOM, "model-a", "upstream", clock())

    unavailable = health.unavailable(["model-a", "model-b"], "upstream")

    assert list(unavailable) == ["model-a"]


def test_health_reports_nothing_measured_as_nothing_rather_than_zero() -> None:
    """A target never called and one that answers instantly are not the same."""
    health = HealthRegistry()

    assert health.of(HealthScope.MODEL, "unused").as_dict()["mean_ttft_seconds"] is None


# ── Retry budget ─────────────────────────────────────────────────────────────


def test_the_budget_stops_the_chain_on_attempts() -> None:
    budget = RetryBudget(max_attempts=2, max_total_seconds=600.0)

    assert budget.exceeded_by(1, 1.0) is None
    assert "2 attempt(s)" in (budget.exceeded_by(2, 1.0) or "")


def test_the_budget_stops_the_chain_on_elapsed_time() -> None:
    budget = RetryBudget(max_attempts=9, max_total_seconds=30.0)

    assert "elapsed" in (budget.exceeded_by(1, 31.0) or "")


def test_the_budget_says_which_ceiling_it_cannot_enforce() -> None:
    """Cost needs M15. A limit silently not applied is worse than an absent one."""
    assert RetryBudget().unenforced == []
    assert "max_total_cost" in RetryBudget(max_total_cost=1.0).unenforced[0]


# ── The attempt chain ────────────────────────────────────────────────────────


def _chain(clock: FakeClock, health: HealthRegistry | None = None,
           budget: RetryBudget | None = None) -> AttemptChain:
    return AttemptChain(
        health=health or HealthRegistry(clock=clock),
        provider="upstream",
        budget=budget or RetryBudget(),
    )


def test_the_chain_walks_primary_then_fallbacks() -> None:
    clock = FakeClock()
    chain = _chain(clock)
    chain.load("primary", ["second", "third"])

    first = chain.next_target()
    chain.failed(first or "", clock(), FailureClass.OVERLOAD)

    assert chain.next_target() == "second"


def test_the_chain_stops_when_the_failure_forbids_a_fallback() -> None:
    """An invalid request fails identically everywhere; §10 does not chase it."""
    clock = FakeClock()
    chain = _chain(clock)
    chain.load("primary", ["second"])
    chain.failed(chain.next_target() or "", clock(), FailureClass.INVALID_REQUEST)

    assert chain.next_target() is None
    assert "not a fallback-eligible failure" in chain.summary()["stopped_because"]


def test_a_connection_failure_retries_the_same_target_once() -> None:
    """The request provably never arrived, so a second attempt is not a duplicate."""
    clock = FakeClock()
    chain = _chain(clock)
    chain.load("primary", ["second"])
    chain.failed(chain.next_target() or "", clock(), FailureClass.CONNECTION)

    assert chain.next_target() == "primary"


def test_the_same_target_is_never_retried_twice() -> None:
    """"Once" was the intent and was not what the code did.

    `failed` re-armed the retry on every connection failure, so a target that
    refused every connection was offered again indefinitely — and the test above
    passed throughout, because it asserted the *first* retry and never asked
    what came after it. It encoded the situation rather than the rule.
    """
    clock = FakeClock()
    chain = _chain(clock, budget=RetryBudget(max_attempts=9, max_total_seconds=600.0))
    chain.load("primary", ["second"])
    chain.failed(chain.next_target() or "", clock(), FailureClass.CONNECTION)
    chain.failed(chain.next_target() or "", clock(), FailureClass.CONNECTION)

    assert chain.next_target() == "second"


def test_a_chain_against_an_upstream_that_refuses_everything_terminates() -> None:
    """The livelock, as a regression test.

    A refused connection is classified `CONNECTION`, which is the one class
    permitted to retry the same target — and the retry was returned *before* the
    budget was consulted, so nothing bounded it. Against a closed LM Studio the
    chain re-offered the primary forever: the second candidate was never
    reached, `max_attempts` was never enforced, and the request never returned.
    A switchboard that cannot fail over is worse than one that fails.

    The loop below is capped only so a regression fails the suite instead of
    hanging it.
    """
    clock = FakeClock()
    chain = _chain(clock)
    chain.load("primary", ["second", "third"])

    tried = []
    for _ in range(20):
        target = chain.next_target()
        if target is None:
            break
        tried.append(target)
        chain.failed(target, clock(), FailureClass.CONNECTION, "connection refused")
    else:  # pragma: no cover - only reached if the livelock returns
        raise AssertionError(f"the chain never ended; it tried {tried}")

    assert tried == ["primary", "primary", "second"]
    assert "retry budget spent" in chain.summary()["stopped_because"]


def test_a_same_target_retry_spends_the_budget_like_any_other_attempt() -> None:
    """A retry is another attempt and another slice of the client's patience."""
    clock = FakeClock()
    chain = _chain(clock, budget=RetryBudget(max_attempts=1, max_total_seconds=600.0))
    chain.load("primary", ["second"])
    chain.failed(chain.next_target() or "", clock(), FailureClass.CONNECTION)

    assert chain.next_target() is None
    assert "retry budget spent" in chain.summary()["stopped_because"]


def test_the_chain_skips_a_candidate_behind_an_open_circuit_and_says_so() -> None:
    """A skipped candidate must appear in the history, or the explanation lies."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=60.0, clock=clock)
    health.record(FailureClass.LOCAL_OOM, "primary", "upstream", clock())
    chain = _chain(clock, health)
    chain.load("primary", ["second"])

    assert chain.next_target() == "second"
    assert chain.summary()["attempts"][0] == {
        "model": "primary",
        "outcome": "skipped",
        "detail": health.of(HealthScope.MODEL, "primary").refusal(),
        # A skipped candidate names the upstream it would have used and carries
        # no timings: no connection was opened, so there is nothing measured to
        # report and `None` says that rather than claiming 0 ms.
        "provider": "upstream",
        "elapsed_ms": None,
        "ttft_ms": None,
    }


def test_the_chain_stops_when_the_budget_is_spent() -> None:
    clock = FakeClock()
    chain = _chain(clock, budget=RetryBudget(max_attempts=1, max_total_seconds=600.0))
    chain.load("primary", ["second", "third"])
    chain.failed(chain.next_target() or "", clock(), FailureClass.OVERLOAD)

    assert chain.next_target() is None
    assert "retry budget spent" in chain.summary()["stopped_because"]


def test_an_interrupted_stream_is_terminal_and_recorded_on_both_scopes() -> None:
    """By the time a stream breaks the client holds part of an answer (§10)."""
    clock = FakeClock()
    health = HealthRegistry(clock=clock)
    chain = _chain(clock, health)
    chain.load("primary", ["second"])
    chain.begin("primary")
    chain.interrupted("primary")

    assert health.of(HealthScope.MODEL, "primary").stream_interruptions == 1
    assert health.of(HealthScope.PROVIDER, "upstream").stream_interruptions == 1
    assert "would corrupt it" in chain.summary()["stopped_because"]


def test_a_success_credits_both_the_model_and_the_provider() -> None:
    """Crediting one leaves the other's circuit stuck open after a recovery."""
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=1.0, clock=clock)
    health.record(FailureClass.CONNECTION, "primary", "upstream", clock())
    clock.advance(2.0)
    chain = _chain(clock, health)
    chain.load("primary", [])

    chain.succeeded("primary", chain.begin("primary"))

    assert health.of(HealthScope.PROVIDER, "upstream").state is BreakerState.CLOSED
    assert health.of(HealthScope.MODEL, "primary").state is BreakerState.CLOSED


def test_a_logged_detail_survives_formatting() -> None:
    """The reason must reach the log, not just the headline.

    Every `extra={"detail": ...}` in this service carries the sentence that
    names what actually happened — which models were tried, which failed, why
    the chain stopped. The formatter used to promote three correlation fields
    and drop this one, so an operator got "no upstream attempt succeeded" and
    nothing to act on. Found while watching a real gateway log.
    """
    import logging

    from ecosystem_protocol import JsonLineFormatter

    record = logging.LogRecord(
        name="ravis.api.openai.chat",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="no upstream attempt succeeded",
        args=(),
        exc_info=None,
    )
    record.detail = "Tried: coder-a (rate_limit), coder-b (rate_limit)."

    rendered = json.loads(JsonLineFormatter().format(record))

    assert rendered["detail"] == "Tried: coder-a (rate_limit), coder-b (rate_limit)."


def test_one_upstreams_open_circuit_does_not_block_another_upstreams_models() -> None:
    """A provider circuit is scoped to that provider, not to the request.

    `AttemptChain.provider` was a single string, chosen when RAVIS talked to one
    upstream. M8 made upstreams plural and the string stayed, which turned a
    naming shortcut into a routing bug: a provider-scoped circuit takes out
    every model behind it, so one failing local runtime excluded the entire
    catalogue — including models on a completely different, healthy upstream.

    Measured before the fix: one label blocked 4 of 4 candidates. After: 2.
    """
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=60.0, clock=clock)
    health.record(FailureClass.CONNECTION, "granite", "lmstudio", clock())

    owner = {"granite": "lmstudio", "qwen": "lmstudio",
             "llama3.2:3b": "ollama", "all-minilm": "ollama"}
    blocked = health.unavailable(list(owner), lambda model: owner[model])

    assert set(blocked) == {"granite", "qwen"}, "only the failed upstream's models"
    assert "llama3.2:3b" not in blocked, "a healthy upstream keeps serving"


def test_the_chain_attributes_each_attempt_to_its_own_upstream() -> None:
    """Failures land on the provider that produced them.

    Otherwise a fallback crossing to a second upstream credits or blames the
    first, and the health record that decides future routing describes a
    provider that was never called.
    """
    clock = FakeClock()
    health = HealthRegistry(failure_threshold=1, cooldown_seconds=60.0, clock=clock)
    owner = {"granite": "lmstudio", "llama3.2:3b": "ollama"}
    chain = AttemptChain(health=health, provider=lambda model: owner[model])
    chain.load("granite", ["llama3.2:3b"])

    assert chain.provider_for("granite") == "lmstudio"
    assert chain.provider_for("llama3.2:3b") == "ollama"

    chain.failed("granite", clock(), FailureClass.CONNECTION)

    assert not health.allows(HealthScope.PROVIDER, "lmstudio")
    assert health.allows(HealthScope.PROVIDER, "ollama"), "untouched by another's failure"


def test_a_successful_attempt_records_its_destination_and_timings() -> None:
    """§11.4 asks an inspector for the upstream destination and the stream's own
    numbers. Both were computed on this path already — `succeeded` needs them for
    the health registry — and then thrown away, so an attempt said only that it
    had worked. "Succeeded" and "succeeded, first byte in 240 ms" are different
    facts, and only the second one lets somebody see a route getting slower."""
    clock = FakeClock()
    health = HealthRegistry(clock=clock)
    chain = _chain(clock, health)

    started = clock()
    clock.advance(3.0)
    chain.succeeded("only", started, ttft=0.24)

    recorded = chain.summary()["attempts"][0]
    assert recorded["provider"] == "upstream"
    assert recorded["ttft_ms"] == pytest.approx(240.0)
    assert recorded["elapsed_ms"] == pytest.approx(3000.0)


def test_a_failed_attempt_is_timed_because_how_long_it_took_is_the_fault() -> None:
    """"Refused instantly" and "refused after thirty seconds" are different
    faults with the same outcome string. Without the timing the record cannot
    separate a rejection from a timeout."""
    clock = FakeClock()
    health = HealthRegistry(clock=clock)
    chain = _chain(clock, health)

    started = clock()
    clock.advance(30.0)
    chain.failed("only", started, FailureClass.TIMEOUT, "no response")

    recorded = chain.summary()["attempts"][0]
    assert recorded["elapsed_ms"] == pytest.approx(30_000.0)
    # No first byte ever arrived, and `None` says so. A 0 here would read as a
    # stream that started instantly and then produced nothing.
    assert recorded["ttft_ms"] is None


def test_the_attempt_record_holds_no_prompt_and_no_completion() -> None:
    """The scope decision behind M11's first half, pinned so a later change has
    to argue with it. Metadata only: what was asked and what came back are not in
    this record and are not meant to be."""
    clock = FakeClock()
    health = HealthRegistry(clock=clock)
    chain = _chain(clock, health)
    chain.succeeded("only", clock(), ttft=0.1)

    fields = set(chain.summary()["attempts"][0])
    assert fields == {"model", "outcome", "detail", "provider", "elapsed_ms", "ttft_ms"}


# ── §10: no failover that crosses a constraint ──────────────────────────────
#
# Both of these were found by adversarially verifying the degradation matrix's
# own claim that no failover is unsafe. Neither was caught by a test, and both
# were reproduced against the real classifier before being fixed.


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadError("connection reset"),
     httpx.WriteError("broken pipe"),
     httpx.RemoteProtocolError("server disconnected"),
     httpx.CloseError("closed")],
    ids=["read", "write", "protocol", "close"],
)
def test_a_transport_failure_after_the_request_left_is_never_re_sent(
    failure: Exception,
) -> None:
    """**The one same-target retry in the system is justified by a sentence
    that only covers `ConnectError`**: "a connection that was never established
    cannot have delivered the request, so a second attempt is provably not a
    duplicate." Every `httpx.TransportError` was landing on it, and these four
    all happen *after* the bytes went out — so a retry re-sends a completion the
    provider may already have run and billed, which is the duplicated charge
    §10 forbids in those words.
    """
    policy = classify_exception(failure).policy

    assert policy.retry_same_target is False
    # Falling back is still right: nothing about the *request* has been shown
    # to be wrong, and the next candidate is a different target.
    assert policy.may_fall_back is True


def test_a_connection_that_never_opened_still_gets_its_one_retry() -> None:
    """The falsifier for the fix. Narrowing the class must not cost the retry
    the reasoning actually supports — a refused connection provably delivered
    nothing, and asking the same target again is free of duplicate risk."""
    policy = classify_exception(httpx.ConnectError("refused")).policy

    assert policy.retry_same_target is True


@pytest.mark.parametrize("status", [401, 403], ids=["unauthenticated", "forbidden"])
def test_a_credential_failure_cannot_be_talked_out_of_by_the_body(status: int) -> None:
    """**Measured against the real classifier, and it was routing around an
    auth failure.** Body markers are read before the status because an
    upstream's own words usually say more than a status code — but a real 403
    reading "Your project does not have access: model is not available" matched
    the model-unavailable markers, classified as `MODEL_UNAVAILABLE`, and that
    class *may fall back*. So a rejected credential was shopped to the next
    provider, which §10 forbids: do not route around a refusal to find a more
    permissive one.
    """
    body = json.dumps({"error": {
        "message": "Your project does not have access: model is not available",
    }}).encode()

    failure = classify_response(status, body)

    assert failure is FailureClass.AUTHENTICATION
    assert failure.policy.may_fall_back is False


def test_the_body_still_speaks_where_the_status_is_coarse() -> None:
    """The falsifier for that one. Making a credential status final must not
    make every status final — a 400 says only "your request", and the body is
    what distinguishes a safety refusal from a malformed field."""
    refusal = json.dumps({"error": {"message": "content_filter triggered"}}).encode()

    assert classify_response(400, refusal) is FailureClass.CONTENT_REFUSAL
