"""The Data Steward: a `DatasetSpec` becomes a frame plus an honest account
of what is wrong with it.

**Deterministic on purpose.** The design lists this among the six agent roles,
but nothing it does here needs a model: aligning calendars, converting
frequency and constructing returns each have exactly one right answer, and a
reproducibility manifest means nothing if the data under it depended on what a
model felt like that morning. The genuinely model-shaped part — mapping the
columns of an uploaded file to roles — belongs to Phase 6, where the user
confirms the mapping before ingest.

The quality report is not decoration. Survivorship and look-ahead are the two
ways a study of returns flatters itself, and neither shows up in a p-value.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

import pandas as pd
from pydantic import BaseModel, Field

from econometrica.agents.schemas import DatasetSpec
from econometrica.econ.fingerprint import fingerprint_frame
from econometrica.econ.returns import to_returns

#: `DatasetSpec.frequency` uses the classic pandas letters, which pandas 3
#: rejects outright — `resample("M")` is a ValueError, not a warning. Period
#: *end* labels throughout: a monthly price is the last price of the month,
#: never the first.
_RESAMPLE_RULE = {"D": None, "W": "W", "M": "ME", "Q": "QE", "A": "YE"}

#: A listing that begins this long after the requested window opened is
#: reported. Roughly a trading month — shorter than that is ordinary calendar
#: noise, longer starts to bias whatever is estimated on it.
LATE_START_DAYS = 30

#: Below this, nothing in the tool registry produces a trustworthy estimate.
MIN_USABLE_OBS = 30


class DataUnavailableError(ValueError):
    """The requested data could not be assembled into a usable frame."""


class PriceSource(Protocol):
    """Where price history comes from.

    Injected rather than imported: Phase 6 owns the yfinance, Stooq, FRED and
    Ken French adapters, and this agent's own behaviour — alignment, frequency,
    returns, quality — needs no network to be tested.
    """

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        """Price history for one ticker, indexed by date."""
        ...


class QualityFlag(BaseModel):
    code: str
    severity: Literal["info", "warning", "risk"]
    detail: str


class DataQualityReport(BaseModel):
    """What the analysis is actually running on."""

    tickers: list[str]
    frequency: str
    return_method: str
    rows: int
    start: date
    end: date
    #: Dates covered by at least one series but not by all of them.
    dropped_rows: int = 0
    #: Of the aligned frame, so a result can be tied back to its input.
    fingerprint: str = ""
    flags: list[QualityFlag] = Field(default_factory=list)

    def has(self, code: str) -> bool:
        return any(flag.code == code for flag in self.flags)

    def flag(self, code: str) -> QualityFlag:
        for flag in self.flags:
            if flag.code == code:
                return flag
        raise KeyError(f"no flag {code!r} in this report")


@dataclass(frozen=True)
class Dataset:
    """Aligned levels, their returns, and what is wrong with both.

    Both frames are carried because the tool registry wants each in different
    places: the unit-root family tests price levels, the volatility family
    fits returns. Columns are the tickers verbatim — binding a tool's `column`
    parameter to one of them is the Econometrician's job, not this one's.
    """

    prices: pd.DataFrame
    returns: pd.DataFrame
    report: DataQualityReport


class DataSteward:
    def __init__(
        self,
        source: PriceSource,
        *,
        min_obs: int = MIN_USABLE_OBS,
        late_start_days: int = LATE_START_DAYS,
    ) -> None:
        self.source = source
        self.min_obs = min_obs
        self.late_start_days = late_start_days

    async def resolve(self, spec: DatasetSpec) -> Dataset:
        raw = await self._fetch(spec)
        flags: list[QualityFlag] = []

        windowed = {
            ticker: self._window(prices, spec, ticker, flags)
            for ticker, prices in raw.items()
        }
        aligned, dropped = self._align(windowed, spec, flags)
        resampled = self._resample(aligned, spec.frequency)

        if len(resampled) < self.min_obs:
            flags.append(
                QualityFlag(
                    code="short_sample",
                    severity="warning",
                    detail=(
                        f"{len(resampled)} observations after alignment; below "
                        f"{self.min_obs}, estimates are not trustworthy"
                    ),
                )
            )

        returns = resampled.apply(lambda column: to_returns(column, method=spec.return_method))

        report = DataQualityReport(
            tickers=list(spec.tickers),
            frequency=spec.frequency,
            return_method=spec.return_method,
            rows=len(resampled),
            start=resampled.index.min().date(),
            end=resampled.index.max().date(),
            dropped_rows=dropped,
            fingerprint=fingerprint_frame(resampled),
            flags=flags,
        )
        return Dataset(prices=resampled, returns=returns, report=report)

    # --- internals ----------------------------------------------------------

    async def _fetch(self, spec: DatasetSpec) -> dict[str, pd.Series]:
        fetched: dict[str, pd.Series] = {}
        for ticker in spec.tickers:
            try:
                fetched[ticker] = await self.source.prices(
                    ticker, start=spec.start, end=spec.end
                )
            except Exception as exc:
                # Name the ticker. "not listed" across a five-asset study is
                # not something a user can act on.
                raise DataUnavailableError(f"{ticker}: {exc}") from exc
        return fetched

    def _window(
        self,
        prices: pd.Series,
        spec: DatasetSpec,
        ticker: str,
        flags: list[QualityFlag],
    ) -> pd.Series:
        index = pd.DatetimeIndex(prices.index)
        start, end = pd.Timestamp(spec.start), pd.Timestamp(spec.end)

        if index.max() > end:
            _add_once(
                flags,
                QualityFlag(
                    code="look_ahead",
                    severity="risk",
                    detail=(
                        f"the source returned data past {spec.end}"
                        f" (to {index.max().date()}); it has been truncated"
                    ),
                ),
            )

        if index.min() > start + pd.Timedelta(days=self.late_start_days):
            flags.append(
                QualityFlag(
                    code="late_start",
                    severity="risk",
                    detail=(
                        f"{ticker} has no data until {index.min().date()},"
                        f" {(index.min() - start).days} days into the requested"
                        " window — treat survivorship and listing bias as live"
                    ),
                )
            )

        return prices.loc[(index >= start) & (index <= end)]

    def _align(
        self,
        windowed: dict[str, pd.Series],
        spec: DatasetSpec,
        flags: list[QualityFlag],
    ) -> tuple[pd.DataFrame, int]:
        union = _union_length(windowed.values())

        frame = pd.concat(windowed, axis=1, join="inner").dropna()
        if frame.empty:
            raise DataUnavailableError(
                f"{', '.join(spec.tickers)} share no overlapping observations"
                f" between {spec.start} and {spec.end}"
            )

        dropped = union - len(frame)
        if dropped > 0:
            flags.append(
                QualityFlag(
                    code="calendar_misalignment",
                    severity="warning",
                    detail=(
                        f"{dropped} date(s) were covered by some series but not all"
                        " and were dropped by the inner join"
                    ),
                )
            )
        return frame, dropped

    @staticmethod
    def _resample(frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
        rule = _RESAMPLE_RULE.get(frequency)
        if rule is None:
            return frame
        return frame.resample(rule).last().dropna()


def _union_length(series: Iterable[pd.Series]) -> int:
    """How many dates any series covered, before the inner join narrowed it."""
    indexes = [pd.DatetimeIndex(item.index) for item in series]
    if not indexes:
        return 0
    union = indexes[0]
    for index in indexes[1:]:
        union = union.union(index)
    return len(union)


def _add_once(flags: list[QualityFlag], flag: QualityFlag) -> None:
    """Record a flag that describes the source rather than one ticker.

    Over-returning is one fact about the source however many tickers it
    happens to, unlike a late listing, which is a fact about each.
    """
    if not any(existing.code == flag.code for existing in flags):
        flags.append(flag)
