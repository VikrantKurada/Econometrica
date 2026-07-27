"""Ken French's factor library, through `pandas-datareader`.

This is what makes `ff3`, `ff5` and `carhart4` reachable. All three have been in
the catalogue every Planner reads since Phase 2, with `factors` defaulting to
`["mkt_rf","smb","hml"]`, and no source could supply a factor column — so
`require_columns` raised and the step landed `failed`. Three of thirty-seven
tools that could never run.

**Factors are not prices**, which is the whole reason they enter through their
own method rather than `PriceSource`. `Mkt-RF` is already a return; differencing
it would give the change in a return, and the result would look entirely
plausible. They join `Dataset.frame` unsuffixed and undifferenced.

Two published properties do the damage if missed:

* **the values are percent.** `Mkt-RF` of `-0.70` is -0.70%, so -0.0070.
  Forgetting it rescales every loading by a factor of a hundred.
* **the index is a pandas `Period`**, `period[D]` or `period[M]`, which joins to
  nothing on a price calendar.

And one constraint that comes from the library rather than the data:
`F-F_Momentum_Factor_daily` cannot be read by pandas-datareader 0.11.1 —
`famafrench.py:118` compares a string index against an int and raises
`TypeError` at every date range — so **`carhart4` is monthly-only** here, and
says so rather than failing inside a library the caller never named.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from econometrica.data.base import DataUnavailableError

#: Takes a dataset name and window, returns the reader's `{0: frame, ...}` dict.
Reader = Callable[..., dict[Any, Any]]

#: Published percent to decimal. The single most common Ken French error.
_PERCENT = 100.0

#: The Data Steward resamples prices with these rules, so factors compounded to
#: a coarser frequency have to use the same bin edges or the join drops rows.
_RESAMPLE_RULE = {"D": None, "W": "W", "M": "ME", "Q": "QE", "A": "YE"}

#: What the steward calls the risk-free column, and what `capm` and every factor
#: model take as their `risk_free` parameter.
RISK_FREE_COLUMN = "risk_free"


@dataclass(frozen=True)
class _Dataset:
    """One Ken French file, and how to read its columns."""

    #: Published at monthly frequency. Every set has one.
    monthly: str
    #: Published daily, or None where the library cannot parse it.
    daily: str | None
    #: Published column name -> the tool parameter name it becomes.
    columns: dict[str, str]


@dataclass(frozen=True)
class FactorSet:
    """A named set of factors, assembled from one or more files."""

    name: str
    datasets: tuple[_Dataset, ...]
    #: Tool parameter names, matching the tool's own `factors` default.
    factors: tuple[str, ...]
    notes: str = ""
    #: Frequencies this set cannot be built at, with the reason.
    unavailable: dict[str, str] = field(default_factory=dict)


_RESEARCH_3 = _Dataset(
    monthly="F-F_Research_Data_Factors",
    daily="F-F_Research_Data_Factors_daily",
    columns={"Mkt-RF": "mkt_rf", "SMB": "smb", "HML": "hml", "RF": RISK_FREE_COLUMN},
)

_RESEARCH_5 = _Dataset(
    monthly="F-F_Research_Data_5_Factors_2x3",
    daily="F-F_Research_Data_5_Factors_2x3_daily",
    columns={
        "Mkt-RF": "mkt_rf",
        "SMB": "smb",
        "HML": "hml",
        "RMW": "rmw",
        "CMA": "cma",
        "RF": RISK_FREE_COLUMN,
    },
)

_MOMENTUM = _Dataset(
    monthly="F-F_Momentum_Factor",
    # Parsed by neither `DataReader` nor `FamaFrenchReader` on 0.11.1.
    daily=None,
    columns={"Mom": "mom"},
)

FACTOR_SETS: dict[str, FactorSet] = {
    "ff3": FactorSet(
        name="ff3",
        datasets=(_RESEARCH_3,),
        factors=("mkt_rf", "smb", "hml"),
        notes="Fama-French three factors: market, size, value.",
    ),
    "ff5": FactorSet(
        name="ff5",
        datasets=(_RESEARCH_5,),
        factors=("mkt_rf", "smb", "hml", "rmw", "cma"),
        notes="Fama-French five factors: adds profitability and investment.",
    ),
    "carhart4": FactorSet(
        name="carhart4",
        # SMB deliberately from the three-factor file: the five-factor file
        # constructs it differently (4.40 against 5.01 for the same month), and
        # Carhart is FF3 plus momentum, not FF5 minus two.
        datasets=(_RESEARCH_3, _MOMENTUM),
        factors=("mkt_rf", "smb", "hml", "mom"),
        notes="Carhart four factors: the three-factor set plus momentum.",
        unavailable={
            "D": (
                "the daily momentum file cannot be parsed by pandas-datareader"
                " 0.11.1 (famafrench.py compares a string index against an int),"
                " so carhart4 is available monthly only"
            ),
            "W": (
                "weekly carhart4 would have to be compounded from the daily"
                " momentum file, which pandas-datareader 0.11.1 cannot parse"
            ),
        },
    ),
}


class FamaFrenchFactorSource:
    """Factor sets, converted to decimals on a timestamp index."""

    label = "Ken French Data Library (Dartmouth)"

    def __init__(self, *, reader: Reader | None = None) -> None:
        self._reader = reader

    async def factors(
        self, factor_set: str, *, start: date, end: date, frequency: str
    ) -> pd.DataFrame:
        """Every factor in the named set, plus its risk-free column."""
        if end <= start:
            raise ValueError(f"the requested window {start}..{end} runs backwards or is empty")

        declared = FACTOR_SETS.get(factor_set)
        if declared is None:
            raise DataUnavailableError(
                f"unknown factor set {factor_set!r}; known sets are"
                f" {', '.join(sorted(FACTOR_SETS))}"
            )

        if frequency in declared.unavailable:
            raise DataUnavailableError(
                f"{factor_set} cannot be built at frequency {frequency!r}:"
                f" {declared.unavailable[frequency]}"
            )

        frames = [
            await self._read_dataset(dataset, declared, start, end, frequency)
            for dataset in declared.datasets
        ]

        # Inner join: a period one file covers and another does not is not a
        # period this set exists for.
        joined = frames[0] if len(frames) == 1 else pd.concat(frames, axis=1, join="inner")
        # Only if two files disagreed about a column name, which would mean a
        # set was declared wrongly rather than anything about the data.
        joined = joined.loc[:, ~joined.columns.duplicated()]
        return joined.dropna(how="all")

    # --- internals ----------------------------------------------------------

    async def _read_dataset(
        self,
        dataset: _Dataset,
        declared: FactorSet,
        start: date,
        end: date,
        frequency: str,
    ) -> pd.DataFrame:
        # Daily where it exists, because compounding a coarser series up is
        # impossible; monthly otherwise. W compounds from daily, Q and A from
        # monthly.
        daily_name = dataset.daily
        use_daily = frequency in ("D", "W") and daily_name is not None
        name = daily_name if use_daily and daily_name is not None else dataset.monthly

        tables = await asyncio.to_thread(self._read, name, declared.name, start, end)

        # Monthly research files return {0: monthly, 1: annual}. Reading key 1
        # would give a handful of annual observations that look like a short
        # sample rather than a wrong table.
        frame = tables.get(0)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise DataUnavailableError(
                f"{declared.name}: {name} returned no observations for"
                f" {start}..{end}"
            )

        frame = frame.rename(columns=dataset.columns)
        wanted = [name for name in dataset.columns.values() if name in frame.columns]
        frame = frame[wanted].astype(float) / _PERCENT
        frame.index = _timestamps(frame.index)

        return _compound(frame, frequency, published_daily=use_daily)

    def _read(
        self, name: str, factor_set: str, start: date, end: date
    ) -> dict[Any, Any]:
        reader = self._reader if self._reader is not None else _famafrench_reader()
        try:
            return reader(name, start=start, end=end)
        except Exception as exc:
            # Named for the set the caller asked for, not only the file it
            # happens to be assembled from — nobody planned `F-F_Momentum_Factor`.
            raise DataUnavailableError(
                f"{factor_set}: could not read {name} — {exc}"
            ) from exc


def _timestamps(index: pd.Index) -> pd.DatetimeIndex:
    """A Period index as period-*end* timestamps.

    Ken French publishes `period[D]` or `period[M]`. A monthly period becomes
    the last day of its month, matching the `ME` labels the Data Steward's own
    resampling produces — `normalize` because `to_timestamp(how="end")` lands on
    the final nanosecond of the period, not on midnight.
    """
    if isinstance(index, pd.PeriodIndex):
        return pd.DatetimeIndex(index.to_timestamp(how="end").normalize())
    return pd.DatetimeIndex(index)


def _compound(frame: pd.DataFrame, frequency: str, *, published_daily: bool) -> pd.DataFrame:
    """Aggregate factor returns to a coarser frequency.

    Returns compound, they do not average or sum: a week's factor return is
    `prod(1+r)-1` over its days. Taking `.last()` as the price path does would
    throw away every period but one.
    """
    rule = _RESAMPLE_RULE.get(frequency)
    if rule is None:
        return frame
    if frequency == "M" and not published_daily:
        return frame  # already monthly, and already labelled at month end
    if frequency in ("Q", "A") or (frequency == "W" and published_daily):
        return (1.0 + frame).resample(rule).prod() - 1.0
    return (1.0 + frame).resample(rule).prod() - 1.0


def _famafrench_reader() -> Reader:
    """Imported on first use, as the other adapters are."""
    from pandas_datareader import famafrench

    def read(name: str, *, start: date, end: date) -> dict[Any, Any]:
        result: dict[Any, Any] = famafrench.FamaFrenchReader(
            name, start=start, end=end
        ).read()
        return result

    return read
