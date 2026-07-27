"""OpenTelemetry spans, persisted where they can be queried.

**Not a second copy of the run trace.** `run_steps` records every model call and
tool invocation with its agent, provider, tokens, cost and latency — that is the
domain record, and the run DAG is drawn from it. Spans cover what it cannot see:
HTTP handlers, database timings, the transport underneath a provider call. Two
sources for the same number is how a cost dashboard starts lying, so tokens live
in `run_steps` and nowhere else.

A plain table rather than a hypertable, unlike `observations`. A single-user
local workbench produces spans in the thousands, not the millions, and a
hypertable's chunking would buy nothing while making the schema harder to
reason about. The indexes that matter are on `trace_id` (draw one trace) and
`started_at` (percentiles over a window).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from econometrica.db.base import Base

SPAN_STATUSES = ("ok", "error", "unset")


class Span(Base):
    """One timed operation, and where it sat in its trace."""

    __tablename__ = "spans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'error', 'unset')", name="ck_spans_status_known"
        ),
        CheckConstraint("duration_ms >= 0", name="ck_spans_duration_non_negative"),
        # A self-edge is not a tree, and it makes a trace viewer loop forever —
        # the same constraint `run_steps` carries for the same reason.
        CheckConstraint(
            "parent_span_id IS NULL OR parent_span_id <> span_id",
            name="ck_spans_no_self_parent",
        ),
    )

    #: OTel ids, hex-encoded. The pair is the identity: a span id is unique
    #: within its trace, and writing the same span twice must not raise —
    #: a retried flush is ordinary.
    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), default=None)

    name: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="internal")
    status: Mapped[str] = mapped_column(String(10), default="unset")
    #: The error, where there was one. Empty otherwise.
    detail: Mapped[str] = mapped_column(Text, default="", server_default="")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)

    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
