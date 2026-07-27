"""FRED series, through `pandas-datareader`.

**FRED publishes series, not prices.** `SP500` is an index level; `DGS3MO` is a
yield in percent per annum; `UNRATE` is a percentage of the labour force. This
adapter returns what was published and converts nothing, because it cannot know
what an arbitrary series id means — interpreting one as a risk-free rate is
`data/rates.py`'s job, where the convention is declared per series.

It satisfies `PriceSource` so that an index level can be analysed like any other
level, and so the on-disk cache applies to it unchanged. That is also what makes
it the **independent cross-check** the project needs now that Stooq is gone: a
live test compares FRED's `SP500` against Yahoo's `^GSPC`, two entirely separate
pipelines for one series.

No API key, which is why it is the second source rather than a keyed vendor.
"""

import asyncio
from collections.abc import Callable
from datetime import date

import pandas as pd

from econometrica.data.base import DataUnavailableError

#: Takes `pandas_datareader.data.DataReader`'s arguments and returns its frame.
Reader = Callable[..., "pd.DataFrame | None"]


class FredSeriesSource:
    """One FRED series at a time, exactly as published."""

    #: "as published" is the load-bearing part: a reader who sees this in a
    #: quality report knows no scaling was applied on the way in.
    label = "FRED (St. Louis Fed, series as published)"

    def __init__(self, *, reader: Reader | None = None) -> None:
        self._reader = reader

    async def prices(self, series_id: str, *, start: date, end: date) -> pd.Series:
        if end <= start:
            raise ValueError(f"the requested window {start}..{end} runs backwards or is empty")

        # pandas-datareader is synchronous and fetches over HTTP.
        frame = await asyncio.to_thread(self._read, series_id, start, end)
        return self._to_series(frame, series_id)

    # --- internals ----------------------------------------------------------

    def _read(self, series_id: str, start: date, end: date) -> pd.DataFrame | None:
        reader = self._reader if self._reader is not None else _datareader()
        return reader(series_id, "fred", start=start, end=end)

    def _to_series(self, frame: pd.DataFrame | None, series_id: str) -> pd.Series:
        if frame is None or frame.empty:
            raise DataUnavailableError(
                f"{series_id}: FRED returned no observations — check the series id"
                " at https://fred.stlouisfed.org/"
            )

        column = series_id if series_id in frame.columns else frame.columns[0]
        series = frame[column].astype(float)
        series.index = pd.DatetimeIndex(series.index)
        # FRED writes a missing observation as `.`, which arrives as NaN. A
        # daily treasury series has one on every market holiday.
        series = series.dropna()

        if series.empty:
            raise DataUnavailableError(
                f"{series_id}: every observation in the requested window is missing"
            )

        series.name = series_id
        return series


def _datareader() -> Reader:
    """Imported on first use, for the same reason yfinance is."""
    from pandas_datareader import data

    reader: Reader = data.DataReader
    return reader
