"""Tracing wired into the running application.

The unit tests prove spans nest and persist; these prove the application
actually produces them, and — the part that matters more — that it keeps
serving requests when the telemetry behind them is broken.
"""

import pytest
from sqlalchemy import select

from econometrica.db.models import Span
from econometrica.telemetry import SpanRecord, configure_tracing, reset_tracing
from econometrica.telemetry.writer import SpanWriter


@pytest.fixture(autouse=True)
def collecting():
    collected: list[SpanRecord] = []
    configure_tracing(sink=collected.append)
    yield collected
    reset_tracing()


async def test_a_request_produces_a_span(client, collecting):
    await client.get("/api/health")

    names = [record.name for record in collecting]
    assert "GET /api/health" in names


async def test_the_span_carries_the_route_and_status(client, collecting):
    await client.get("/api/health")

    record = next(r for r in collecting if r.name == "GET /api/health")
    assert record.attributes["http.method"] == "GET"
    assert record.attributes["http.status_code"] == 200
    assert record.status == "ok"


async def test_a_failing_request_is_recorded_as_an_error(client, collecting):
    from uuid import uuid4

    await client.get(f"/api/runs/{uuid4()}")

    record = next(r for r in collecting if "runs" in r.name)
    assert record.attributes["http.status_code"] == 404
    assert record.status == "error"


async def test_the_metrics_endpoint_does_not_trace_itself_into_a_loop(client, collecting):
    """It would only ever measure itself, and every reading would add a row
    that changed the next one."""
    await client.get("/api/metrics")

    assert not [r for r in collecting if "metrics" in r.name]


# --- the writer ----------------------------------------------------------------


async def test_the_writer_drains_its_queue_into_postgres(session):
    writer = SpanWriter(sessionmaker=_maker(session))
    writer.submit(_record("http.request"))
    writer.submit(_record("db.query", span_id="2"))

    await writer.drain()

    rows = (await session.scalars(select(Span))).all()
    assert {row.name for row in rows} == {"http.request", "db.query"}


async def test_a_full_queue_drops_rather_than_blocks(session):
    """A slow database must never become back-pressure on the request path.
    Losing a measurement is the correct trade; stalling a user's request to
    keep one is not."""
    writer = SpanWriter(sessionmaker=_maker(session), capacity=2)

    for index in range(10):
        writer.submit(_record("http.request", span_id=str(index)))

    assert writer.dropped == 8
    assert writer.pending == 2


async def test_a_writer_whose_database_is_down_keeps_accepting(session):
    """The failure this exists to survive. The drain swallows it, the queue
    keeps taking spans, and the application never learns."""

    def broken():
        raise RuntimeError("the database is gone")

    writer = SpanWriter(sessionmaker=broken)
    writer.submit(_record("http.request"))

    await writer.drain()

    writer.submit(_record("http.request", span_id="2"))
    assert writer.pending == 1


def _maker(session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def maker():
        yield session

    return maker


def _record(name: str, span_id: str = "1") -> SpanRecord:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return SpanRecord(
        trace_id=span_id.rjust(32, "0"),
        span_id=span_id.rjust(16, "0"),
        parent_span_id=None,
        name=name,
        kind="server",
        status="ok",
        detail="",
        started_at=now,
        ended_at=now,
        duration_ms=1.0,
    )


async def test_parametrised_routes_group_under_one_name(client, collecting):
    """The defect a live server exposed: middleware runs before routing, so the
    span was named from the raw path and every run id became its own bucket. A
    p95 over a thousand distinct paths describes nothing."""
    from uuid import uuid4

    await client.get(f"/api/runs/{uuid4()}")
    await client.get(f"/api/runs/{uuid4()}")

    names = [record.name for record in collecting if "runs" in record.name]
    assert names == ["GET /api/runs/{run_id}", "GET /api/runs/{run_id}"]


async def test_the_route_attribute_is_the_template_too(client, collecting):
    from uuid import uuid4

    await client.get(f"/api/runs/{uuid4()}")

    record = next(r for r in collecting if "runs" in r.name)
    assert record.attributes["http.route"] == "/api/runs/{run_id}"
