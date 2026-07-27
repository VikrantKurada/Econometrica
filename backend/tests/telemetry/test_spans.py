"""OpenTelemetry spans, persisted where they can be queried.

**Not a second copy of the run trace.** `run_steps` already records every model
call and tool invocation with its agent, provider, tokens, cost and latency —
that is the domain record, and a run's DAG is drawn from it. Spans cover what it
cannot see: HTTP handlers, database timings, the transport underneath a provider
call. A test asserts the token totals are read from one place, because two
sources of the same number is how a cost dashboard starts lying.

The other property under test is that telemetry cannot break the thing it is
measuring. A span sink that raises, or a database that is down, must cost the
measurement and nothing else.
"""

import asyncio

import pytest
from sqlalchemy import select

from econometrica.db.models import Span
from econometrica.telemetry import (
    SpanRecord,
    configure_tracing,
    record_spans,
    reset_tracing,
    span,
)


@pytest.fixture(autouse=True)
def tracing():
    """A collecting sink per test, so nothing reaches the real writer."""
    collected: list[SpanRecord] = []
    configure_tracing(sink=collected.append)
    yield collected
    reset_tracing()


# --- shape --------------------------------------------------------------------


async def test_a_span_records_its_name_and_duration(tracing):
    with span("db.query"):
        await asyncio.sleep(0.01)

    assert len(tracing) == 1
    assert tracing[0].name == "db.query"
    assert tracing[0].duration_ms >= 5.0


async def test_attributes_are_carried(tracing):
    with span("http.request", attributes={"route": "/api/runs", "method": "POST"}):
        pass

    assert tracing[0].attributes["route"] == "/api/runs"


async def test_a_span_that_raises_is_recorded_as_an_error(tracing):
    with pytest.raises(ValueError), span("tool.execute"):
        raise ValueError("boom")

    assert tracing[0].status == "error"
    assert "boom" in tracing[0].detail


async def test_a_span_that_completes_is_recorded_as_ok(tracing):
    with span("tool.execute"):
        pass

    assert tracing[0].status == "ok"


# --- nesting ------------------------------------------------------------------


async def test_a_child_span_records_its_parent(tracing):
    with span("run.execute"):  # noqa: SIM117 — the nesting is what this test is about
        with span("tool.capm"):
            pass

    child = next(record for record in tracing if record.name == "tool.capm")
    parent = next(record for record in tracing if record.name == "run.execute")
    assert child.parent_span_id == parent.span_id
    assert child.trace_id == parent.trace_id


async def test_nesting_survives_an_await(tracing):
    """The whole reason this is worth having in an async application. Context
    propagates through contextvars; if it did not, every span inside an awaited
    call would come back as its own root and the trace would be flat."""

    async def inner():
        await asyncio.sleep(0)
        with span("db.select"):
            await asyncio.sleep(0)

    with span("http.request"):
        await inner()

    child = next(record for record in tracing if record.name == "db.select")
    parent = next(record for record in tracing if record.name == "http.request")
    assert child.parent_span_id == parent.span_id


async def test_concurrent_work_does_not_cross_traces(tracing):
    """Two requests in flight at once must not adopt each other's spans."""

    async def request(name: str):
        with span(f"http.{name}"):
            await asyncio.sleep(0.01)
            with span(f"db.{name}"):
                await asyncio.sleep(0)

    await asyncio.gather(request("a"), request("b"))

    by_name = {record.name: record for record in tracing}
    assert by_name["db.a"].trace_id == by_name["http.a"].trace_id
    assert by_name["db.b"].trace_id == by_name["http.b"].trace_id
    assert by_name["http.a"].trace_id != by_name["http.b"].trace_id


async def test_a_root_span_has_no_parent(tracing):
    with span("http.request"):
        pass

    assert tracing[0].parent_span_id is None


# --- telemetry must not break what it measures --------------------------------


async def test_a_sink_that_raises_does_not_break_the_span(tracing):
    """Injected on purpose. A metrics pipeline that can fail a user's request
    is worse than no metrics."""

    def explode(record: SpanRecord) -> None:
        raise RuntimeError("the telemetry database is down")

    configure_tracing(sink=explode)

    result = []
    with span("http.request"):
        result.append("the request still ran")

    assert result == ["the request still ran"]


async def test_tracing_that_was_never_configured_is_inert(tracing):
    """Import order and startup failures must not turn `span` into a landmine."""
    reset_tracing()

    with span("http.request"):
        pass  # no sink, no error


# --- persistence ---------------------------------------------------------------


async def test_spans_reach_postgres_with_their_ids_intact(session):
    with span("http.request"):  # noqa: SIM117 — the nesting is what this test is about
        with span("db.select", attributes={"table": "runs"}):
            pass

    collected: list[SpanRecord] = []
    configure_tracing(sink=collected.append)
    with span("run.execute"):  # noqa: SIM117 — the nesting is what this test is about
        with span("tool.capm"):
            pass

    await record_spans(session, collected)

    rows = (await session.scalars(select(Span))).all()
    assert {row.name for row in rows} == {"run.execute", "tool.capm"}
    child = next(row for row in rows if row.name == "tool.capm")
    parent = next(row for row in rows if row.name == "run.execute")
    assert child.parent_span_id == parent.span_id
    assert child.trace_id == parent.trace_id
    assert child.duration_ms >= 0


async def test_writing_the_same_span_twice_is_not_an_error(session):
    """A retried flush must not fail the writer. Spans are idempotent by id."""
    collected: list[SpanRecord] = []
    configure_tracing(sink=collected.append)
    with span("http.request"):
        pass

    await record_spans(session, collected)
    await record_spans(session, collected)

    rows = (await session.scalars(select(Span))).all()
    assert len(rows) == 1


async def test_recording_nothing_is_harmless(session):
    await record_spans(session, [])

    assert (await session.scalars(select(Span))).all() == []


# --- OTLP is opt-in ------------------------------------------------------------


async def test_no_otlp_exporter_is_configured_without_an_endpoint():
    """Off unless asked for. A telemetry stack that tried to reach a collector
    nobody runs would retry on a background thread for the life of the process,
    on every developer's machine."""
    from econometrica.telemetry.spans import otlp_configured

    configure_tracing(sink=lambda record: None)

    assert otlp_configured() is False


async def test_an_endpoint_turns_the_otlp_exporter_on():
    """Configured, not connected. The exporter batches and retries on its own
    thread, so nothing here touches the network — which is why this can assert
    against a collector that does not exist."""
    from econometrica.telemetry.spans import otlp_configured

    configure_tracing(
        sink=lambda record: None, otlp_endpoint="http://localhost:4318/v1/traces"
    )

    assert otlp_configured() is True


async def test_an_unreachable_collector_does_not_break_a_span():
    from econometrica.telemetry.spans import otlp_configured

    collected: list[SpanRecord] = []
    configure_tracing(
        sink=collected.append, otlp_endpoint="http://127.0.0.1:9/v1/traces"
    )

    with span("http.request"):
        pass

    assert otlp_configured() is True
    assert [record.name for record in collected] == ["http.request"]


async def test_resetting_shuts_the_otlp_exporter_down():
    """Otherwise one test that configured a collector keeps a retry thread
    alive for the rest of the process, and the noise outlives the suite."""
    from econometrica.telemetry.spans import otlp_configured

    configure_tracing(sink=lambda record: None, otlp_endpoint="http://127.0.0.1:9/v1/traces")
    assert otlp_configured() is True

    reset_tracing()

    assert otlp_configured() is False
