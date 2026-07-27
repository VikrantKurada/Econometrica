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
from econometrica.data.base import DataUnavailableError, PriceSource

# Imported at module level, which is only possible because the protocol moved
# down into `data/base.py`. While it lived here, this line was a cycle.
from econometrica.data.rates import resolve_rate
from econometrica.econ.fingerprint import fingerprint_frame
from econometrica.econ.returns import PERIODS_PER_YEAR, to_returns

# Re-exported, not merely imported. Both names were defined here until the
# fifth adapter made the layering plain: `data/` is the lower layer and had been
# reaching up into `agents/` for its own vocabulary. They now live in
# `data/base.py`, and keeping them importable from here means none of the
# sixteen existing import sites had to change.
__all__ = [
    "LATE_START_DAYS",
    "MIN_USABLE_OBS",
    "DataQualityReport",
    "DataSteward",
    "DataUnavailableError",
    "Dataset",
    "PriceSource",
    "QualityFlag",
]

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

#: Fraction of observations a factor set must cover before the shortfall is
#: reported. Ken French publishes on a US trading calendar and lags the present
#: by a month or two, so a little slack is ordinary; a lot means the study is
#: quietly running on a fraction of its window.
FACTOR_COVERAGE = 0.9


class FactorSource(Protocol):
    """Where a named factor set comes from.

    Deliberately not a `PriceSource`. A factor set is one object — `ff3` means
    three columns on a shared calendar, plus the risk-free rate they are excess
    of — and splitting it into series to fit a protocol designed for price
    history would mean fetching the same file once per column.
    """

    async def factors(
        self, factor_set: str, *, start: date, end: date, frequency: str
    ) -> pd.DataFrame:
        """Decimal factor returns, indexed by date, named for tool parameters."""
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
    #: Which adapter the prices came from.
    source: str = ""
    rows: int
    start: date
    end: date
    #: Dates covered by at least one series but not by all of them.
    dropped_rows: int = 0
    #: The series id the risk-free column was built from, or None. The column
    #: itself is named for the tool parameter, so this is the only record of
    #: *which* rate an excess return was taken against.
    risk_free: str | None = None
    #: The factor set joined onto the frame, or None.
    factors: str | None = None
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
    #: Per-period risk-free returns, already rescaled and re-based to the
    #: frame's frequency. Not in `returns`: it is a return already, and
    #: differencing it would give the change in the rate.
    risk_free: pd.Series | None = None
    #: Factor returns, already decimal and on the frame's calendar. Also not in
    #: `returns`, and for the same reason.
    factors: pd.DataFrame | None = None

    #: Suffix distinguishing a return column from the level it came from.
    RETURN_SUFFIX = "_return"

    #: `capm` and every factor model call this parameter `risk_free`, so the
    #: column has to carry that name for a plan to bind to it without knowing
    #: which series was fetched. The series id goes in the quality report.
    RISK_FREE_COLUMN = "risk_free"

    @property
    def frame(self) -> pd.DataFrame:
        """Levels and returns in one frame, which is what a tool takes.

        `ToolFn` accepts a single DataFrame, so a plan step cannot say "the
        returns frame" — it says a column name, and the name is what carries
        the distinction. The first return is NaN rather than absent, so the
        two stay index-aligned; every tool drops it.
        """
        parts: list[pd.DataFrame | pd.Series] = [
            self.prices,
            self.returns.add_suffix(self.RETURN_SUFFIX),
        ]
        if self.factors is not None:
            parts.append(self.factors)
        # After the factors, so an explicitly requested rate overwrites the one
        # the factor file carried — which is what `mixed_risk_free` warns about.
        if self.risk_free is not None:
            parts.append(self.risk_free.rename(Dataset.RISK_FREE_COLUMN))
        combined = pd.concat(parts, axis=1)
        return combined.loc[:, ~combined.columns.duplicated(keep="last")]


class DataSteward:
    def __init__(
        self,
        source: PriceSource,
        *,
        rate_source: PriceSource | None = None,
        factor_source: FactorSource | None = None,
        min_obs: int = MIN_USABLE_OBS,
        late_start_days: int = LATE_START_DAYS,
    ) -> None:
        self.source = source
        # Separate from `source` because a rate lives somewhere else: Yahoo
        # cannot serve DGS3MO, and FRED cannot serve an equity. None is
        # allowed, and a spec that then asks for a rate is refused rather than
        # quietly analysed without one.
        self.rate_source = rate_source
        # And factors are not prices at all — a whole set arrives as one frame,
        # already in decimals, already returns.
        self.factor_source = factor_source
        self.min_obs = min_obs
        self.late_start_days = late_start_days

    async def resolve(self, spec: DatasetSpec) -> Dataset:
        raw = await self._fetch(spec)
        flags: list[QualityFlag] = []

        source = getattr(self.source, "label", "") or type(self.source).__name__
        if "synthetic" in source.lower():
            # `risk`, not `info`. A reader who misses this misreads everything
            # built on top of it, which is a worse failure than any of the
            # sampling problems flagged below.
            flags.append(
                QualityFlag(
                    code="synthetic_data",
                    severity="risk",
                    detail=(
                        f"these prices came from {source} — they are generated,"
                        " not market data, and no conclusion drawn from them"
                        " describes a real asset"
                    ),
                )
            )

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
        index = pd.DatetimeIndex(resampled.index)

        factors = await self._resolve_factors(spec, index, flags)
        risk_free = await self._resolve_risk_free(spec, index)

        risk_free_label = spec.risk_free
        if factors is not None and Dataset.RISK_FREE_COLUMN in factors.columns:
            if risk_free is None:
                risk_free = factors[Dataset.RISK_FREE_COLUMN]
                risk_free_label = f"{spec.factors} RF"
            else:
                # Both were supplied. The plan asked for the explicit one, so it
                # wins — but these factors are excess returns against their own
                # RF, and subtracting a different rate puts two definitions of
                # "risk-free" in one regression.
                flags.append(
                    QualityFlag(
                        code="mixed_risk_free",
                        severity="warning",
                        detail=(
                            f"the {spec.factors} factors are excess returns over their"
                            f" own risk-free rate, but {spec.risk_free} was requested"
                            " and has been used instead — the regression mixes two"
                            " definitions of the risk-free rate"
                        ),
                    )
                )
            factors = factors.drop(columns=[Dataset.RISK_FREE_COLUMN])

        # Both are part of the input the results came from, so both belong in
        # the fingerprint: without them, a CAPM on excess returns and the same
        # CAPM on raw returns would claim to be the same analysis.
        fingerprinted = pd.concat(
            [resampled, *(f for f in (factors,) if f is not None), *(
                [risk_free] if risk_free is not None else []
            )],
            axis=1,
        )

        report = DataQualityReport(
            tickers=list(spec.tickers),
            frequency=spec.frequency,
            return_method=spec.return_method,
            source=source,
            rows=len(resampled),
            start=resampled.index.min().date(),
            end=resampled.index.max().date(),
            dropped_rows=dropped,
            risk_free=risk_free_label,
            factors=spec.factors,
            fingerprint=fingerprint_frame(fingerprinted),
            flags=flags,
        )
        return Dataset(
            prices=resampled,
            returns=returns,
            report=report,
            risk_free=risk_free,
            factors=factors,
        )

    # --- internals ----------------------------------------------------------

    async def _resolve_factors(
        self, spec: DatasetSpec, index: pd.DatetimeIndex, flags: list[QualityFlag]
    ) -> pd.DataFrame | None:
        """The named factor set, on the price frame's calendar.

        Reindexed rather than forward-filled: a factor return belongs to its
        own period, and carrying one forward would invent a second period with
        the same return. Rows the set does not cover stay NaN — every factor
        model drops them through `align_series` — and the count is flagged
        rather than left as a surprise in the tool's `nobs`.
        """
        if not spec.factors:
            return None

        if self.factor_source is None:
            raise DataUnavailableError(
                f"the plan asks for the {spec.factors} factor set but no factor"
                " source is configured, so the factor models cannot run. Either"
                " supply one or plan a single-factor model such as capm."
            )

        frame = await self.factor_source.factors(
            spec.factors, start=spec.start, end=spec.end, frequency=spec.frequency
        )
        aligned = frame.reindex(index)

        covered = int(aligned.notna().all(axis=1).sum())
        if covered < len(index) * FACTOR_COVERAGE:
            flags.append(
                QualityFlag(
                    code="factor_coverage",
                    severity="warning",
                    detail=(
                        f"the {spec.factors} factors cover {covered} of {len(index)}"
                        " observations; the rest will be dropped by the factor model."
                        " The library publishes on a US trading calendar and lags the"
                        " present by a month or two"
                    ),
                )
            )
        return aligned

    async def _resolve_risk_free(
        self, spec: DatasetSpec, index: pd.DatetimeIndex
    ) -> pd.Series | None:
        """The risk-free rate as per-period returns on the frame's calendar.

        Nothing happens unless the plan asked for one. When it did and no rate
        source is configured, this refuses: dropping it would run a CAPM on raw
        rather than excess returns, which answers a different question, and
        nothing downstream could tell that it had happened. That silent drop is
        what this field did for the whole of Phases 4 and 5.
        """
        if not spec.risk_free:
            return None

        if self.rate_source is None:
            raise DataUnavailableError(
                f"the plan asks for a risk-free rate ({spec.risk_free}) but no rate"
                " source is configured, so excess returns cannot be built. Either"
                " supply one or plan without a risk-free rate — running on raw"
                " returns instead would answer a different question silently."
            )

        try:
            rate = await resolve_rate(
                self.rate_source,
                spec.risk_free,
                start=spec.start,
                end=spec.end,
                periods_per_year=PERIODS_PER_YEAR.get(spec.frequency, 252),
            )
        except DataUnavailableError:
            raise
        except Exception as exc:
            raise DataUnavailableError(f"{spec.risk_free}: {exc}") from exc

        # Forward-filled onto the price calendar: a treasury series has a gap on
        # every market holiday, and a rate persists across one rather than
        # vanishing. `bfill` covers a frame that opens before the rate's first
        # published date.
        aligned = rate.reindex(index, method="ffill")
        if bool(aligned.isna().any()):
            aligned = aligned.bfill()
        return aligned

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
