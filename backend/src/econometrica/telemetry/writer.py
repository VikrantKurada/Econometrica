"""Getting finished spans out of the request path and into Postgres.

A bounded queue and a task that drains it. The queue is the whole design: the
request thread hands a span over and never waits, so a slow or absent database
cannot become back-pressure on a user's request.

**A full queue drops.** Losing a measurement is the correct trade; stalling a
request to keep one is not. The count of what was dropped is kept, because a
dashboard that quietly under-reports is worse than one that says how much it
missed.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any

from econometrica.telemetry.spans import SpanRecord, record_spans

#: Spans held before dropping. Large enough that a burst of requests survives a
#: momentary stall, small enough that an unreachable database costs bounded
#: memory rather than growing until something else fails.
DEFAULT_CAPACITY = 2048

#: How many spans one transaction writes. A drain that took the whole queue in
#: one statement would hold a connection for as long as the backlog.
BATCH = 200

SessionMaker = Callable[[], AbstractAsyncContextManager[Any]]


class SpanWriter:
    """Accepts spans without blocking; writes them when it gets the chance."""

    def __init__(
        self, *, sessionmaker: SessionMaker, capacity: int = DEFAULT_CAPACITY
    ) -> None:
        self._sessionmaker = sessionmaker
        self._queue: list[SpanRecord] = []
        self._capacity = capacity
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.dropped = 0

    @property
    def pending(self) -> int:
        return len(self._queue)

    def submit(self, record: SpanRecord) -> None:
        """Take a span, or drop it. Never raises, never waits."""
        if len(self._queue) >= self._capacity:
            self.dropped += 1
            return
        self._queue.append(record)

    async def drain(self) -> int:
        """Write everything queued. Failures cost the spans and nothing else."""
        written = 0
        while self._queue:
            batch, self._queue = self._queue[:BATCH], self._queue[BATCH:]
            try:
                async with self._sessionmaker() as session:
                    written += await record_spans(session, batch)
                    await session.commit()
            except Exception:
                # The database being unavailable is not this component's problem
                # to solve, and re-queueing would grow without bound.
                continue
        return written

    async def run(self, *, interval: float = 2.0) -> None:
        """Drain on a timer until stopped."""
        while not self._stopping:
            await asyncio.sleep(interval)
            await self.drain()

    def start(self, *, interval: float = 2.0) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self.run(interval=interval))

    async def stop(self) -> None:
        """Stop draining, then write whatever is left."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            # CancelledError derives from BaseException, so both are named.
            with suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self.drain()
