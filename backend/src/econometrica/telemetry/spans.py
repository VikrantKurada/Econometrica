"""Creating spans, and getting them into Postgres.

The OpenTelemetry API does the hard part — `contextvars`-based context, so a
span opened before an `await` is still the parent of one opened after it. What
this module adds is a sink that the application controls, because the SDK's
batch processor exports from its own thread and the only database driver here is
async.

So: the tracer provider gets a processor whose `on_end` converts the span to a
plain `SpanRecord` and hands it to a sink. In the application the sink is a
bounded queue that a background task drains through the async session; in tests
it is a list. Either way `on_end` runs on the loop thread that ended the span and
never blocks.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.db.models import Span

#: Longest error text kept on a span. A stack trace belongs in the log; a span
#: is an index into one.
_DETAIL_LIMIT = 500

#: How long an OTLP export may take before it is abandoned. Telemetry is the
#: last thing that should delay a shutdown.
OTLP_TIMEOUT_SECONDS = 2


@dataclass(frozen=True)
class SpanRecord:
    """One finished span, detached from the SDK.

    A plain record rather than a `ReadableSpan` so that everything downstream —
    the queue, the writer, the tests — is free of SDK types, the same boundary
    `llm/types.py` draws around provider SDKs.
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    status: str
    detail: str
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    attributes: dict[str, Any] = field(default_factory=dict)


Sink = Callable[[SpanRecord], None]

_provider: TracerProvider | None = None
_sink: Sink | None = None
_otlp: bool = False


class _RecordingProcessor(SpanProcessor):
    """Converts each finished span and hands it to the current sink."""

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        sink = _sink
        if sink is None:
            return
        # The one rule of this module. A sink that raises costs the measurement
        # and nothing else — never the request that produced it.
        with suppress(Exception):
            sink(_to_record(span))

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def configure_tracing(*, sink: Sink, otlp_endpoint: str = "") -> None:
    """Point tracing at a sink, and optionally at an OTLP collector.

    OTLP is **off unless an endpoint is configured**. A telemetry stack that
    tried to reach a collector nobody runs would retry on a background thread
    for the life of the process, on every machine this is developed on.
    """
    global _provider, _sink, _otlp

    _sink = sink
    if _provider is None:
        # Deliberately *not* registered as the global tracer provider. `span()`
        # takes its tracer from this object directly, context propagation lives
        # in contextvars rather than in the provider, and the global can only be
        # set once per process — which would make the provider impossible to
        # replace, and a batch exporter impossible to shut down.
        _provider = TracerProvider()
        _provider.add_span_processor(_RecordingProcessor())

    if otlp_endpoint and not _otlp:
        _add_otlp(_provider, otlp_endpoint)


def _add_otlp(provider: TracerProvider, endpoint: str) -> None:
    """Attach the OTLP exporter, or carry on without it.

    Batched on its own thread, so nothing is sent from the request path and an
    unreachable collector costs nothing but the spans it never received.
    """
    global _otlp
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(
            BatchSpanProcessor(
                # Short on purpose. The default is 10s with retries, which a
                # shutdown waits out — so an unreachable collector would hold
                # the process closed long after the user asked it to stop.
                OTLPSpanExporter(endpoint=endpoint, timeout=OTLP_TIMEOUT_SECONDS),
                export_timeout_millis=OTLP_TIMEOUT_SECONDS * 1000,
            )
        )
        _otlp = True
    except Exception:
        # An export path that cannot be built is a lost measurement, not a
        # failed startup.
        _otlp = False


def otlp_configured() -> bool:
    return _otlp


def reset_tracing() -> None:
    """Detach the sink and dispose the provider.

    Shutting the provider down is what stops a batch exporter's thread. Without
    it a single test that configured OTLP against a collector nobody runs would
    keep retrying for the life of the process, and the noise would outlive the
    suite.
    """
    global _provider, _sink, _otlp

    _sink = None
    _otlp = False
    if _provider is not None:
        # A provider that fails to shut down is still a provider we are done
        # with; refusing to let go of it would strand the next configuration.
        with suppress(Exception):
            _provider.shutdown()
        _provider = None


def tracing_configured() -> bool:
    return _sink is not None


@contextmanager
def span(name: str, *, attributes: dict[str, Any] | None = None) -> Any:
    """Time a block of work as a span.

    Inert when tracing was never configured, which matters because import order
    and a failed startup must not turn this into a landmine at every call site.
    """
    if _provider is None:
        yield None
        return

    tracer = _provider.get_tracer("econometrica")
    with tracer.start_as_current_span(name) as current:
        if attributes:
            for key, value in attributes.items():
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.set_status(Status(StatusCode.ERROR, str(exc)[:_DETAIL_LIMIT]))
            raise
        else:
            # Only when nothing inside said otherwise. A block can complete
            # without raising and still have recorded a failure — an HTTP 404
            # is the case that found this, where stamping OK on the way out
            # erased the status the middleware had just set.
            if getattr(current, "status", None) is None or (
                current.status.status_code is StatusCode.UNSET  # type: ignore[attr-defined]
            ):
                current.set_status(Status(StatusCode.OK))


async def record_spans(session: AsyncSession, records: Sequence[SpanRecord]) -> int:
    """Write spans, ignoring ones already stored.

    `ON CONFLICT DO NOTHING` because a retried flush is ordinary and must not
    raise: the trace and span ids are the identity, so re-writing one is a
    no-op rather than a conflict to resolve.
    """
    if not records:
        return 0

    await session.execute(
        insert(Span)
        .values([_to_row(record) for record in records])
        .on_conflict_do_nothing(index_elements=["trace_id", "span_id"])
    )
    await session.flush()
    return len(records)


# --- conversion ---------------------------------------------------------------


def _to_record(span: ReadableSpan) -> SpanRecord:
    # A finished span always carries a context; the SDK types it optional for
    # the non-recording case, which never reaches a processor.
    context = span.get_span_context()
    assert context is not None
    start = span.start_time or time.time_ns()
    end = span.end_time or start

    status = "unset"
    if span.status.status_code is StatusCode.OK:
        status = "ok"
    elif span.status.status_code is StatusCode.ERROR:
        status = "error"

    return SpanRecord(
        trace_id=format(context.trace_id, "032x"),
        span_id=format(context.span_id, "016x"),
        parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
        name=span.name,
        kind=span.kind.name.lower().removeprefix("span_kind_"),
        status=status,
        detail=(span.status.description or "")[:_DETAIL_LIMIT],
        started_at=datetime.fromtimestamp(start / 1e9, tz=UTC),
        ended_at=datetime.fromtimestamp(end / 1e9, tz=UTC),
        duration_ms=(end - start) / 1e6,
        attributes={str(k): v for k, v in dict(span.attributes or {}).items()},
    )


def _to_row(record: SpanRecord) -> dict[str, Any]:
    return {
        "trace_id": record.trace_id,
        "span_id": record.span_id,
        "parent_span_id": record.parent_span_id,
        "name": record.name,
        "kind": record.kind,
        "status": record.status,
        "detail": record.detail,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "duration_ms": max(record.duration_ms, 0.0),
        "attributes": record.attributes,
    }


def records_from(spans: Iterable[ReadableSpan]) -> list[SpanRecord]:
    """Exposed for the OTLP path, which hands back SDK spans."""
    return [_to_record(span) for span in spans]
