"""The Data Steward turns a DatasetSpec into a frame the tools can run on.

Deliberately deterministic — no model is consulted. Calendar alignment,
frequency conversion and return construction have exactly one right answer,
and a reproducibility manifest is worthless if the data behind it depended on
what a model felt like doing that morning.
"""

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import pytest

from econometrica.agents.data_steward import DataSteward, DataUnavailableError
from econometrica.agents.schemas import DatasetSpec


def series(start: str = "2020-01-01", periods: int = 60, step: float = 1.0) -> pd.Series:
    index = pd.date_range(start, periods=periods, freq="D")
    return pd.Series([100.0 + step * i for i in range(periods)], index=index, dtype=float)


@dataclass
class FakeSource:
    """Stands in for the Phase 6 market-data adapters."""

    data: dict[str, pd.Series] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        self.asked.append(ticker)
        if ticker not in self.data:
            raise LookupError(f"{ticker} is not listed")
        return self.data[ticker]


def spec(**overrides: object) -> DatasetSpec:
    payload: dict[str, object] = {
        "tickers": ["AAA"],
        "start": date(2020, 1, 1),
        "end": date(2020, 3, 31),
    }
    payload.update(overrides)
    return DatasetSpec(**payload)  # type: ignore[arg-type]


async def test_prices_are_inner_joined_on_the_shared_calendar():
    """Two assets on different calendars must not be silently paired by row."""
    source = FakeSource(
        {"AAA": series("2020-01-01", 60), "BBB": series("2020-01-11", 60)}
    )

    dataset = await DataSteward(source).resolve(spec(tickers=["AAA", "BBB"]))

    assert list(dataset.prices.columns) == ["AAA", "BBB"]
    assert len(dataset.prices) == 50
    # 70 dates are covered by at least one series; 20 by only one of them.
    assert dataset.report.dropped_rows == 20
    assert dataset.report.has("calendar_misalignment")


async def test_returns_are_built_with_the_requested_method():
    source = FakeSource({"AAA": series(periods=40)})

    log = await DataSteward(source).resolve(spec(return_method="log"))
    simple = await DataSteward(source).resolve(spec(return_method="simple"))

    # A return series loses its first observation, whichever method is used.
    assert len(log.returns) == 39
    assert log.returns["AAA"].iloc[0] == pytest.approx(0.00995033, abs=1e-6)
    assert simple.returns["AAA"].iloc[0] == pytest.approx(0.01)


async def test_a_ticker_that_lists_late_is_flagged_as_a_survivorship_risk():
    """A window silently starting where the data starts flatters the result."""
    source = FakeSource({"AAA": series("2020-02-15", 40)})

    dataset = await DataSteward(source).resolve(spec())

    assert dataset.report.has("late_start")
    assert "AAA" in dataset.report.flag("late_start").detail


async def test_data_beyond_the_window_is_flagged_and_truncated():
    """Look-ahead is the failure that makes a backtest look brilliant."""
    source = FakeSource({"AAA": series("2020-01-01", 200)})

    dataset = await DataSteward(source).resolve(spec())

    assert dataset.report.has("look_ahead")
    assert dataset.prices.index.max().date() <= date(2020, 3, 31)


async def test_no_overlap_raises_rather_than_returning_an_empty_frame():
    source = FakeSource(
        {"AAA": series("2020-01-01", 10), "BBB": series("2020-03-01", 10)}
    )

    with pytest.raises(DataUnavailableError, match="overlap"):
        await DataSteward(source).resolve(spec(tickers=["AAA", "BBB"]))


async def test_a_ticker_the_source_cannot_resolve_names_itself():
    source = FakeSource({"AAA": series()})

    with pytest.raises(DataUnavailableError, match="BBB"):
        await DataSteward(source).resolve(spec(tickers=["AAA", "BBB"]))


async def test_a_sample_too_short_to_estimate_on_is_flagged():
    source = FakeSource({"AAA": series(periods=12)})

    dataset = await DataSteward(source).resolve(spec())

    assert dataset.report.has("short_sample")


async def test_a_clean_single_asset_carries_no_flags():
    source = FakeSource({"AAA": series("2020-01-01", 91)})

    dataset = await DataSteward(source).resolve(spec())

    assert dataset.report.flags == []
    assert dataset.report.rows == 91


async def test_frequency_conversion_resamples_to_period_end():
    """pandas 3 rejects the 'M'/'Q'/'A' aliases DatasetSpec uses outright."""
    source = FakeSource({"AAA": series("2020-01-01", 91)})

    dataset = await DataSteward(source).resolve(spec(frequency="M"))

    assert len(dataset.prices) == 3
    assert [ts.month for ts in dataset.prices.index] == [1, 2, 3]


async def test_the_source_is_named_in_the_report():
    """Which adapter produced the numbers is part of reproducing them."""
    source = FakeSource({"AAA": series()})

    dataset = await DataSteward(source).resolve(spec())

    assert dataset.report.source


async def test_synthetic_data_is_flagged_as_a_risk_not_a_footnote():
    """A run built on generated prices must say so, loudly.

    The synthetic source exists so the pipeline can run before Phase 6. The
    one way that becomes dishonest is if a reader cannot tell.
    """

    class Generated(FakeSource):
        label = "synthetic (generated, not market data)"

    dataset = await DataSteward(Generated({"AAA": series()})).resolve(spec())

    assert dataset.report.has("synthetic_data")
    assert dataset.report.flag("synthetic_data").severity == "risk"


async def test_the_frame_offers_levels_and_returns_under_distinct_names():
    """A tool takes one DataFrame, so both have to coexist in it.

    The unit-root family tests levels and the volatility family fits returns;
    the column name is what tells a plan step which it is asking for.
    """
    source = FakeSource({"AAA": series(periods=40)})

    dataset = await DataSteward(source).resolve(spec())

    assert list(dataset.frame.columns) == ["AAA", "AAA_return"]
    # The first return is undefined, not zero; tools drop it.
    assert bool(dataset.frame["AAA_return"].isna().iloc[0])
    assert len(dataset.frame) == 40


async def test_the_report_fingerprints_the_frame_it_describes():
    """Reproducibility has to reach the data, not only the estimates."""
    source = FakeSource({"AAA": series()})

    first = await DataSteward(source).resolve(spec())
    second = await DataSteward(source).resolve(spec())

    assert first.report.fingerprint == second.report.fingerprint
    assert len(first.report.fingerprint) == 64
