"""Every log line written while serving a request carries that request's IDs.

**The promotion existed; the values never arrived.** `JsonLineFormatter` has
always copied `request_id`, `trace_id` and `application_id` onto the record when
they are set, and §4.3 fixes that vocabulary across the ecosystem so a collector
can join a Clarvis request to a RAVIS route to a SIRVIS benchmark. Nothing ever
set them: a repository-wide search for `extra=` carrying any of the three
returned nothing in any of the three services, so no log line in the ecosystem
was correlated with anything, and `ECOSYSTEM_RUNBOOK.md` §15's "logs, events and
traces are correlated" was false on its first noun.

**A context variable rather than an argument at each call site.** The
alternative is passing IDs into every `logger.info` in four packages, which is
the kind of rule that holds for a month: the one call added later without them
is invisible, and it is always the one being read during an incident. A context
variable is set once where the request is already being identified — each
service's correlation middleware — and a filter attaches it to whatever is
logged underneath, including the lines uvicorn writes, which no call-site change
could ever reach.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from ecosystem_protocol.observability import (
    CorrelationFilter,
    JsonLineFormatter,
    carrying,
    correlation,
)


@pytest.fixture()
def written() -> Any:
    """A logger wired the way `configure_logging` wires the real one."""
    records: list[str] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = Collector()
    handler.setFormatter(JsonLineFormatter())
    handler.addFilter(CorrelationFilter())
    logger = logging.getLogger("test.correlation")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, records


def test_a_line_written_inside_a_request_carries_its_ids(written: Any) -> None:
    logger, records = written
    with carrying(
        request_id="req-1", trace_id="trace-1", application_id="clarvis", session_id="sess-1"
    ):
        logger.info("routing")
    payload = json.loads(records[-1])
    assert payload["request_id"] == "req-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["application_id"] == "clarvis"
    assert payload["session_id"] == "sess-1"
    assert payload["message"] == "routing"


def test_a_line_written_outside_a_request_carries_none_of_them(written: Any) -> None:
    """Absent, not empty.

    A field present and blank reads as "this request had no trace", which is a
    different and wrong claim: startup logging belongs to no request at all.
    """
    logger, records = written
    logger.info("starting up")
    payload = json.loads(records[-1])
    assert "request_id" not in payload
    assert "trace_id" not in payload
    assert "application_id" not in payload
    assert "session_id" not in payload


def test_session_id_reaches_the_line_it_was_added_for(written: Any) -> None:
    """The regression this file did not catch until reverifying §15.

    `carrying()` has accepted `session_id` since the event envelope needed it
    (§4.4), and `CorrelationFilter` copies it onto a record exactly the way it
    copies `request_id` — but `JsonLineFormatter`'s promotion tuple never named
    it, so it rode as far as the record and stopped there. Every test above
    happened to assert `request_id` and `trace_id`, which were both promoted
    correctly, so nothing here ever exercised the field that was not. Isolated
    from the general carries-its-ids test so a future field gets its own
    assertion rather than riding along with `session_id`'s.
    """
    logger, records = written
    with carrying(session_id="sess-only"):
        logger.info("one field, on its own")
    assert json.loads(records[-1])["session_id"] == "sess-only"


def test_the_context_does_not_leak_past_the_request(written: Any) -> None:
    logger, records = written
    with carrying(request_id="req-2", trace_id="trace-2"):
        logger.info("inside")
    logger.info("after")
    assert json.loads(records[-2])["request_id"] == "req-2"
    assert "request_id" not in json.loads(records[-1])


def test_an_explicit_value_at_the_call_site_still_wins(written: Any) -> None:
    """The filter fills gaps; it does not overrule a caller.

    A line that names a *different* request — a background task acting on behalf
    of one — is telling the truth about itself, and a filter that overwrote it
    would replace a fact with the ambient default.
    """
    logger, records = written
    with carrying(request_id="ambient", trace_id="trace-3"):
        logger.info("acting for another", extra={"request_id": "explicit"})
    payload = json.loads(records[-1])
    assert payload["request_id"] == "explicit"
    assert payload["trace_id"] == "trace-3"


def test_the_current_correlation_is_readable() -> None:
    """Readable, because the event publisher needs the same values."""
    assert correlation() == {}
    with carrying(request_id="req-4", trace_id="trace-4"):
        assert correlation() == {"request_id": "req-4", "trace_id": "trace-4"}
    assert correlation() == {}


def test_nesting_restores_the_outer_request(written: Any) -> None:
    logger, records = written
    with carrying(request_id="outer", trace_id="t"):
        with carrying(request_id="inner", trace_id="t"):
            logger.info("in")
        logger.info("back")
    assert json.loads(records[-2])["request_id"] == "inner"
    assert json.loads(records[-1])["request_id"] == "outer"
