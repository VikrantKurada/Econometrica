"""An ingested upload, served through the same protocol as a fetched ticker.

This is what the whole upload path is for. Above `PriceSource` nothing knows
where a series came from — the Data Steward aligns it, the tools run on it, the
manifest fingerprints it — so a user's own file becomes analysable by satisfying
one small interface, not by teaching anything upstream about uploads.

Which means the same honesty rules apply. The label names the file and when it
was ingested, because which upload produced a number is part of reproducing it;
and it must never contain the word `synthetic`, on which the Data Steward's risk
flag fires by substring. A user's own data reported as generated would be as
wrong as the reverse.

**Levels are preferred, but a returns-only file still resolves.** Not every
upload carries prices. Refusing one that carries only returns would make a
legitimate file unusable for the sake of a name.
"""

from datetime import date
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.data.base import DataUnavailableError
from econometrica.db.models import Observation

#: Which field to serve when a symbol has more than one. A file mapping both a
#: close and a volume has two rows per instant, and `prices` means the price.
_PREFERENCE = ("price", "return", "factor", "volume")


class UploadedPriceSource:
    """One ingested dataset, one symbol at a time."""

    def __init__(
        self,
        session: AsyncSession,
        dataset_id: UUID,
        *,
        label: str = "uploaded dataset",
    ) -> None:
        self._session = session
        self._dataset_id = dataset_id
        self._label = label

    @property
    def label(self) -> str:
        return self._label

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        if end <= start:
            raise ValueError(f"the requested window {start}..{end} runs backwards or is empty")

        rows = (
            await self._session.execute(
                select(Observation.ts, Observation.field, Observation.value)
                .where(
                    Observation.dataset_id == self._dataset_id,
                    Observation.symbol == ticker,
                    Observation.ts >= pd.Timestamp(start, tz="UTC"),
                    Observation.ts <= pd.Timestamp(end, tz="UTC"),
                )
                .order_by(Observation.ts)
            )
        ).all()

        if not rows:
            raise DataUnavailableError(
                f"{ticker}: this upload carries no observations between {start}"
                f" and {end}"
            )

        frame = pd.DataFrame(rows, columns=["ts", "field", "value"])
        field = next((name for name in _PREFERENCE if name in set(frame["field"])), None)
        if field is None:
            raise DataUnavailableError(
                f"{ticker}: this upload carries no usable field for it"
            )

        chosen = frame[frame["field"] == field]
        # Stored as timestamptz; the Data Steward windows against a naive
        # Timestamp, and comparing the two raises.
        index = pd.DatetimeIndex(pd.to_datetime(chosen["ts"], utc=True)).tz_localize(None)
        return pd.Series(
            chosen["value"].astype(float).to_numpy(), index=index, name=ticker
        )
