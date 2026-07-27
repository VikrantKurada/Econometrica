"""Writing a run's trace to the database.

The orchestrator builds `StepRecord`s and knows nothing about SQLAlchemy; this
module knows nothing about agents beyond the shape of what they produced. The
one interesting piece of work is turning list positions into foreign keys:
records point at each other by index because nothing has an id until it is
written, and ids only exist after the flush.

Steps are inserted in the order the run produced them, so `Step.seq` — an
identity column — reproduces that order on read. Sorting on `created_at`
would not: the whole run is written in one transaction, so every row carries
the same timestamp to the microsecond.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.agents.orchestrator import RunOutcome
from econometrica.agents.trace import StepRecord
from econometrica.db.models import Run, Step


async def record_run(
    session: AsyncSession, *, chat_id: UUID, tier: str, outcome: RunOutcome
) -> Run:
    """Persist a finished run and its steps. Does not commit."""
    run = Run(
        chat_id=chat_id,
        question=outcome.question,
        status=outcome.status,
        tier=tier,
        revisions=outcome.revisions,
        error=outcome.error,
        # The trace below says what the run did; this says what it produced.
        # `mode="json"` because the column is JSONB: dates and UUIDs have to be
        # strings by the time psycopg sees them.
        outcome=outcome.model_dump(mode="json"),
    )
    session.add(run)
    # The run needs an id before its steps can reference it, and the steps
    # need ids before they can reference each other.
    await session.flush()

    written: list[Step] = []
    for record in outcome.trace:
        step = _to_step(record, run_id=run.id, written=written)
        session.add(step)
        # Flushed one at a time so a step's parent already has an id. A run
        # has a handful of steps, not thousands, so the round trips are cheap
        # next to the model calls that produced them.
        await session.flush()
        written.append(step)

    _accumulate_totals(run, written)
    await session.flush()
    return run


def _to_step(record: StepRecord, *, run_id: UUID, written: list[Step]) -> Step:
    parent_id = None
    if record.parent is not None and 0 <= record.parent < len(written):
        parent_id = written[record.parent].id

    return Step(
        run_id=run_id,
        parent_id=parent_id,
        agent=record.agent,
        kind=record.kind,
        status=record.status,
        attempt=record.attempt,
        provider=record.provider,
        model=record.model,
        input_tokens=record.usage.input_tokens,
        output_tokens=record.usage.output_tokens,
        cache_read_tokens=record.usage.cache_read_tokens,
        cache_write_tokens=record.usage.cache_write_tokens,
        latency_ms=record.latency_ms,
        tool=record.tool,
        tool_call_hash=record.tool_call_hash,
        detail=record.detail,
        prompt=record.prompt,
        response=record.response,
    )


def _accumulate_totals(run: Run, steps: list[Step]) -> None:
    """Denormalise the run's totals.

    Stored rather than computed on read so listing many runs does not
    aggregate the whole step table to fill one column. Rejected attempts count
    — they were billed.
    """
    run.input_tokens = sum(step.input_tokens for step in steps)
    run.output_tokens = sum(step.output_tokens for step in steps)
    run.cache_read_tokens = sum(step.cache_read_tokens for step in steps)
    run.cache_write_tokens = sum(step.cache_write_tokens for step in steps)
    run.cost_usd = sum(step.cost_usd for step in steps)
    run.latency_ms = sum(step.latency_ms for step in steps)
