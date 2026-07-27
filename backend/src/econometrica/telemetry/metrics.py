"""The numbers §8 of the design asks for, each from exactly one source.

Latency percentiles and database timings come from `spans`, because nothing else
sees an HTTP handler or a query. Tokens, cost and the pipeline's own rates come
from `run_steps`, because that is the record of what was billed.

**Nothing is summed from both.** A cost that counted each model call twice would
look entirely plausible and be entirely wrong, so `spans` carries no token or
cost column at all — the separation is structural, not a convention, and a test
asserts the columns do not exist.

Rates with no denominator come back as `None` rather than `0.0`. Zero reads as
"nothing ever failed", which is a claim; "nothing has run yet" is a different
statement and the dashboard should be able to make it.
"""

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Float, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.db.models import Run, Span, Step


class SpanMetric(BaseModel):
    """Timing for one kind of operation."""

    name: str
    count: int
    p50: float
    p95: float
    p99: float
    error_rate: float


class TokenTotals(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


class TokensBy(TokenTotals):
    """Token totals under one provider or one role."""

    key: str
    cost_usd: float = 0.0


class Metrics(BaseModel):
    spans: list[SpanMetric] = Field(default_factory=list)
    tokens: TokenTotals = Field(default_factory=TokenTotals)
    tokens_by_provider: list[TokensBy] = Field(default_factory=list)
    tokens_by_agent: list[TokensBy] = Field(default_factory=list)
    cost_usd: float = 0.0

    runs: int = 0
    revisions_total: int = 0
    revisions_mean: float | None = None
    #: `None` where nothing of that kind has run. See the module docstring.
    tool_error_rate: float | None = None
    validator_rejection_rate: float | None = None


async def collect_metrics(session: AsyncSession) -> Metrics:
    return Metrics(
        spans=await _span_metrics(session),
        tokens=await _token_totals(session),
        tokens_by_provider=await _tokens_grouped(session, Step.provider),
        tokens_by_agent=await _tokens_grouped(session, Step.agent),
        cost_usd=await _scalar(session, select(func.coalesce(func.sum(Step.cost_usd), 0.0))),
        runs=await _scalar(session, select(func.count()).select_from(Run)),
        revisions_total=await _scalar(
            session, select(func.coalesce(func.sum(Run.revisions), 0))
        ),
        revisions_mean=await _optional(session, select(func.avg(Run.revisions))),
        tool_error_rate=await _rate(session, Step.kind == "tool", Step.status == "failed"),
        validator_rejection_rate=await _rate(
            session, Step.agent == "validator", Step.status == "refused"
        ),
    )


# --- spans ---------------------------------------------------------------------


async def _span_metrics(session: AsyncSession) -> list[SpanMetric]:
    def percentile(fraction: float) -> Any:
        return func.percentile_cont(fraction).within_group(Span.duration_ms.asc())

    rows = await session.execute(
        select(
            Span.name,
            func.count().label("count"),
            percentile(0.50),
            percentile(0.95),
            percentile(0.99),
            func.avg(case((Span.status == "error", 1.0), else_=0.0)).cast(Float),
        )
        .group_by(Span.name)
        .order_by(Span.name)
    )

    return [
        SpanMetric(
            name=name,
            count=count,
            p50=float(p50 or 0.0),
            p95=float(p95 or 0.0),
            p99=float(p99 or 0.0),
            error_rate=float(errors or 0.0),
        )
        for name, count, p50, p95, p99, errors in rows.all()
    ]


# --- steps ----------------------------------------------------------------------

_TOKEN_COLUMNS = (
    Step.input_tokens,
    Step.output_tokens,
    Step.cache_read_tokens,
    Step.cache_write_tokens,
)


async def _token_totals(session: AsyncSession) -> TokenTotals:
    row = (
        await session.execute(
            select(*(func.coalesce(func.sum(column), 0) for column in _TOKEN_COLUMNS))
        )
    ).one()
    return TokenTotals(
        input=row[0], output=row[1], cache_read=row[2], cache_write=row[3]
    )


async def _tokens_grouped(session: AsyncSession, column: Any) -> list[TokensBy]:
    rows = await session.execute(
        select(
            column,
            *(func.coalesce(func.sum(token), 0) for token in _TOKEN_COLUMNS),
            func.coalesce(func.sum(Step.cost_usd), 0.0),
        )
        .where(column.is_not(None))
        .group_by(column)
        .order_by(column)
    )
    return [
        TokensBy(
            key=str(key),
            input=row_input,
            output=row_output,
            cache_read=cache_read,
            cache_write=cache_write,
            cost_usd=float(cost),
        )
        for key, row_input, row_output, cache_read, cache_write, cost in rows.all()
    ]


async def _rate(session: AsyncSession, population: Any, failure: Any) -> float | None:
    """The share of ``population`` that met ``failure``, or None if empty."""
    row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((failure, 1), else_=0)), 0),
            ).where(population)
        )
    ).one()
    total, failed = row
    return float(failed) / float(total) if total else None


# --- helpers ---------------------------------------------------------------------


async def _scalar(session: AsyncSession, statement: Any) -> Any:
    return (await session.execute(statement)).scalar_one()


async def _optional(session: AsyncSession, statement: Any) -> float | None:
    value = (await session.execute(statement)).scalar_one_or_none()
    return float(value) if value is not None else None
