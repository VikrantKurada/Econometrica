"""Non-functional telemetry: how long things took, and what failed.

**Deliberately not a second run trace.** `services/tracing.py` records every
model call and tool invocation as a `Step` — agent, provider, model, tokens,
cost, latency, parent links — and the canvas draws the run DAG from it. That is
the domain record and it stays. Spans cover what it cannot see: HTTP handlers,
database timings, the transport under a provider call. Tokens and cost are read
from `run_steps` and nowhere else, because two sources for one number is how a
cost dashboard starts lying.

**Telemetry may never break what it measures.** Every path here swallows its own
failures: a sink that raises, a database that is down, tracing that was never
configured at all. Losing a measurement is a cost worth paying; losing a user's
request to measure it is not.

The OpenTelemetry API is used for span creation because its context propagation
is `contextvars`-based and therefore survives an `await` — which is the whole
reason a trace in an async application is worth drawing. Export is ours: spans
go to Postgres through the application's own async session, and to OTLP only
when an endpoint is configured.
"""

from econometrica.telemetry.spans import (
    SpanRecord,
    configure_tracing,
    otlp_configured,
    record_spans,
    reset_tracing,
    span,
    tracing_configured,
)

__all__ = [
    "SpanRecord",
    "configure_tracing",
    "otlp_configured",
    "record_spans",
    "reset_tracing",
    "span",
    "tracing_configured",
]
