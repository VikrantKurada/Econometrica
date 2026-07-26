"""Yahoo Finance prices, through `yfinance`.

The first real `PriceSource`. Everything above the protocol — planning, gates,
diagnostics, grounding, charts, exports — was built and tested against the
synthetic source, so this is an addition rather than a change: the synthetic
source stays, and stays flagged.

**Three things here are beliefs about yfinance 1.5, and all three were wrong in
the parent plan.** Each is load-bearing, so each has a live test:

* ``auto_adjust=True`` is now the default, and it *removes* ``Adj Close``
  instead of adding to it. Asking for an unadjusted frame is the only way to
  keep the choice of policy — and to keep it visible.
* an unknown ticker does not raise. It comes back as an empty frame with a log
  line, which is the one failure mode a caller cannot see.
* ``end`` is exclusive. Passing the requested end straight through loses the
  last trading day of every window.

The adjustment policy is fixed rather than exposed. Total-return prices are the
right input for every tool in the pricing family — a CAPM beta estimated on
price returns is measuring something else — so a `DatasetSpec` field would only
let a Planner choose wrongly. It is named in `label` instead, because that is
what reaches `DataQualityReport.source`, and on AAPL days before its 2020 split
the two policies differ by 3.1%: reproducing a number means knowing which one
produced it.
"""

import asyncio
from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

from econometrica.agents.data_steward import DataUnavailableError

#: Takes `yfinance.download`'s arguments and returns its frame. Injected so the
#: adapter's own logic is testable without the network, and typed loosely
#: because pinning yfinance's ~17-parameter signature here would break on the
#: next release for no benefit.
Downloader = Callable[..., "pd.DataFrame | None"]

#: Split- *and* dividend-adjusted. Yahoo serves it for equities, indices, FX
#: and crypto alike, so its absence is an anomaly rather than a case to fall
#: back from.
_ADJUSTED_CLOSE = "Adj Close"


class YahooPriceSource:
    """Daily adjusted closes for one ticker at a time."""

    #: Read by the Data Steward into the quality report. It names the
    #: adjustment policy because the vendor alone does not identify the number.
    label = "yfinance (Yahoo, split- and dividend-adjusted close)"

    def __init__(self, *, downloader: Downloader | None = None, timeout: float = 30.0) -> None:
        self._downloader = downloader
        self._timeout = timeout

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        if end <= start:
            raise ValueError(f"the requested window {start}..{end} runs backwards or is empty")

        # yfinance is synchronous and reaches the network through curl_cffi;
        # there is no async entry point. Left on the loop, one fetch would
        # stall every other request for its duration.
        frame = await asyncio.to_thread(self._download, ticker, start, end)
        return self._to_series(frame, ticker)

    # --- internals ----------------------------------------------------------

    def _download(self, ticker: str, start: date, end: date) -> pd.DataFrame | None:
        download = self._downloader if self._downloader is not None else _yfinance_download()
        return download(
            ticker,
            start=start.isoformat(),
            # Exclusive, so the requested end has to be pushed out a day.
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            multi_level_index=False,
            # We are already on a worker thread and only ever ask for one
            # ticker; yfinance's own pool would just add threads under it.
            threads=False,
            timeout=self._timeout,
        )

    def _to_series(self, frame: pd.DataFrame | None, ticker: str) -> pd.Series:
        if frame is None or frame.empty:
            raise DataUnavailableError(
                f"{ticker}: yahoo returned no observations — the symbol is"
                " unlisted, delisted, or spelled differently there"
            )

        if isinstance(frame.columns, pd.MultiIndex):
            frame = _flatten(frame, ticker)

        if _ADJUSTED_CLOSE not in frame.columns:
            raise DataUnavailableError(
                f"{ticker}: the frame carries no {_ADJUSTED_CLOSE!r} column, so the"
                f" adjustment policy this source promises cannot be honoured;"
                f" columns were {sorted(map(str, frame.columns))}"
            )

        series = frame[_ADJUSTED_CLOSE].astype(float)
        series.index = _naive(frame.index)
        series = series.dropna()

        if series.empty:
            raise DataUnavailableError(
                f"{ticker}: every observation in the requested window is missing"
            )

        series.name = ticker
        return series


def _flatten(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Drop yfinance's ticker column level.

    `multi_level_index=False` asks for a flat header, but the level is present
    for a single ticker in some versions and the flag is not honoured
    everywhere, so this does not assume either way.
    """
    if "Ticker" in (frame.columns.names or []):
        try:
            return frame.xs(ticker, axis=1, level="Ticker")
        except KeyError:
            # Yahoo may echo a differently-cased or resolved symbol. Only one
            # ticker was ever requested, so dropping the level is unambiguous.
            pass
    return frame.droplevel(-1, axis=1)


def _naive(index: pd.Index) -> pd.DatetimeIndex:
    """A tz-naive DatetimeIndex.

    The Data Steward windows against `pd.Timestamp(spec.start)`, which is
    naive, and comparing that to an aware index raises. `tz_localize(None)`
    rather than `tz_convert(None)`: yfinance labels bars in the exchange's own
    timezone, and it is the exchange-local *date* that identifies a daily bar.
    """
    converted = pd.DatetimeIndex(index)
    if converted.tz is not None:
        converted = converted.tz_localize(None)
    return converted


def _yfinance_download() -> Downloader:
    """Imported on first use, not at module scope.

    Importing yfinance pulls in curl_cffi and its certificate bundle, which is
    slow enough to notice on application startup — and every test that injects
    a downloader has no reason to pay for it.
    """
    import yfinance

    download: Downloader = yfinance.download
    return download
