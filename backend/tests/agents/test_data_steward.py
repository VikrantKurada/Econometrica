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


# --- the risk-free rate ------------------------------------------------------
#
# `DatasetSpec.risk_free` existed from Phase 4 and reached nothing: the field
# was declared, the planner prompt showed it, and `resolve` iterated
# `spec.tickers` only. A Planner that set it had it silently dropped — the
# exact failure `PlanStep`'s unknown-parameter check exists to prevent, one
# layer up. These are the tests that closed it.


@dataclass
class FakeRateSource:
    """A rate source is an ordinary `PriceSource`; only the convention differs."""

    data: dict[str, pd.Series] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)
    label: str = "fake rates (as published)"

    async def prices(self, series_id: str, *, start: date, end: date) -> pd.Series:
        self.asked.append(series_id)
        if series_id not in self.data:
            raise LookupError(f"{series_id} is not published")
        return self.data[series_id]


def published_rate(value: float = 5.46, start: str = "2020-01-01", periods: int = 60):
    index = pd.date_range(start, periods=periods, freq="D")
    return pd.Series([value] * periods, index=index, dtype=float, name="DGS3MO")


async def test_the_risk_free_series_reaches_the_frame():
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate()})

    dataset = await DataSteward(source, rate_source=rates).resolve(
        spec(risk_free="DGS3MO")
    )

    assert "risk_free" in dataset.frame.columns
    assert rates.asked == ["DGS3MO"]


async def test_the_risk_free_column_is_named_for_the_tool_parameter_not_the_series():
    """`capm` and every factor model take `risk_free`, so that is what the
    column has to be called for a plan to bind to it without knowing which
    series was fetched. Which series it was belongs in the report."""
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate()})

    dataset = await DataSteward(source, rate_source=rates).resolve(
        spec(risk_free="DGS3MO")
    )

    assert list(dataset.frame.columns) == ["AAA", "AAA_return", "risk_free"]
    assert dataset.report.risk_free == "DGS3MO"


async def test_the_risk_free_rate_is_converted_to_the_frames_frequency():
    """5.46% per annum is not 5.46 per day. Subtracting the published number
    would leave an alpha wrong by a factor of a hundred, with no error to
    show for it."""
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate(5.46)})

    dataset = await DataSteward(source, rate_source=rates).resolve(
        spec(risk_free="DGS3MO")
    )

    assert dataset.frame["risk_free"].iloc[0] == pytest.approx(
        1.0546 ** (1 / 252) - 1, rel=1e-12
    )


async def test_a_monthly_frame_gets_a_monthly_risk_free_rate():
    source = FakeSource({"AAA": series(periods=400)})
    rates = FakeRateSource({"DGS3MO": published_rate(5.46, periods=400)})

    dataset = await DataSteward(source, rate_source=rates, min_obs=3).resolve(
        spec(risk_free="DGS3MO", frequency="M", end=date(2021, 1, 31))
    )

    assert dataset.frame["risk_free"].iloc[0] == pytest.approx(
        1.0546 ** (1 / 12) - 1, rel=1e-12
    )


async def test_the_risk_free_rate_is_never_differenced():
    """It is already a return. `to_returns` over it would produce the change in
    the rate, which is not what any tool's `risk_free` parameter means — and
    the result would look plausible."""
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate()})

    dataset = await DataSteward(source, rate_source=rates).resolve(
        spec(risk_free="DGS3MO")
    )

    assert "risk_free_return" not in dataset.frame.columns
    assert "risk_free" not in dataset.returns.columns
    # A constant rate differenced would be zero everywhere.
    assert dataset.frame["risk_free"].iloc[0] > 0


async def test_the_risk_free_rate_is_aligned_onto_the_price_calendar():
    """A treasury series has a gap on every market holiday, and the price frame
    has its own. The rate persists across a gap rather than vanishing."""
    source = FakeSource({"AAA": series(periods=40)})
    sparse = published_rate(periods=40).drop(
        pd.date_range("2020-01-10", periods=3, freq="D")
    )
    rates = FakeRateSource({"DGS3MO": sparse})

    dataset = await DataSteward(source, rate_source=rates).resolve(
        spec(risk_free="DGS3MO")
    )

    assert len(dataset.frame) == 40
    assert dataset.frame["risk_free"].notna().all()


async def test_asking_for_a_risk_free_rate_with_no_rate_source_is_refused():
    """Refusing beats dropping it. A CAPM run on raw rather than excess returns
    answers a different question, and nothing downstream could tell."""
    source = FakeSource({"AAA": series()})

    with pytest.raises(DataUnavailableError, match="risk-free"):
        await DataSteward(source).resolve(spec(risk_free="DGS3MO"))


async def test_an_unresolvable_risk_free_series_names_it():
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({})

    with pytest.raises(DataUnavailableError, match="NOTASERIES"):
        await DataSteward(source, rate_source=rates).resolve(spec(risk_free="NOTASERIES"))


async def test_no_risk_free_column_appears_when_none_was_asked_for():
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate()})

    dataset = await DataSteward(source, rate_source=rates).resolve(spec())

    assert "risk_free" not in dataset.frame.columns
    assert rates.asked == []
    assert dataset.report.risk_free is None


async def test_the_risk_free_rate_changes_the_fingerprint():
    """It is part of the input the results came from, so a manifest that
    ignored it would claim two different analyses were the same one."""
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate()})

    without = await DataSteward(source, rate_source=rates).resolve(spec())
    with_rate = await DataSteward(source, rate_source=rates).resolve(
        spec(risk_free="DGS3MO")
    )

    assert without.report.fingerprint != with_rate.report.fingerprint


# --- factor sets -------------------------------------------------------------
#
# `ff3`, `ff5` and `carhart4` were in the catalogue every Planner reads from
# Phase 2 onward and could never run: nothing could supply a factor column, so
# `require_columns` raised and the step landed `failed`. These are the tests
# that made them reachable.


@dataclass
class FakeFactorSource:
    frame: pd.DataFrame | None = None
    asked: list[tuple[str, str]] = field(default_factory=list)
    error: Exception | None = None

    async def factors(
        self, factor_set: str, *, start: date, end: date, frequency: str
    ) -> pd.DataFrame:
        self.asked.append((factor_set, frequency))
        if self.error is not None:
            raise self.error
        assert self.frame is not None
        return self.frame


def factor_frame(start: str = "2020-01-01", periods: int = 60) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "mkt_rf": [0.001] * periods,
            "smb": [0.0005] * periods,
            "hml": [-0.0002] * periods,
            "risk_free": [0.0001] * periods,
        },
        index=index,
    )


async def test_factor_columns_reach_the_frame():
    source = FakeSource({"AAA": series()})
    factors = FakeFactorSource(factor_frame())

    dataset = await DataSteward(source, factor_source=factors).resolve(
        spec(factors="ff3")
    )

    assert {"mkt_rf", "smb", "hml"} <= set(dataset.frame.columns)
    assert factors.asked == [("ff3", "D")]
    assert dataset.report.factors == "ff3"


async def test_factor_columns_are_never_differenced():
    """A factor is already a return. Differencing it gives the change in a
    return, which is not what any factor model means — and would look
    plausible."""
    source = FakeSource({"AAA": series()})

    dataset = await DataSteward(source, factor_source=FakeFactorSource(factor_frame())).resolve(
        spec(factors="ff3")
    )

    assert "mkt_rf_return" not in dataset.frame.columns
    assert "mkt_rf" not in dataset.returns.columns
    # Constant factor returns differenced would be zero everywhere.
    assert dataset.frame["mkt_rf"].iloc[0] == pytest.approx(0.001)


async def test_the_factor_files_own_risk_free_rate_is_used():
    """Ken French factors are excess returns against *their* RF, so a factor
    study needs no separate rate source and must not be given a different one
    by default."""
    source = FakeSource({"AAA": series()})

    dataset = await DataSteward(source, factor_source=FakeFactorSource(factor_frame())).resolve(
        spec(factors="ff3")
    )

    assert dataset.frame["risk_free"].iloc[0] == pytest.approx(0.0001)
    assert dataset.report.risk_free == "ff3 RF"


async def test_an_explicit_risk_free_rate_alongside_factors_is_flagged():
    """Honoured, because the plan asked for it, but flagged: the factors are
    excess returns against their own RF, so subtracting a different rate mixes
    two definitions in one regression."""
    source = FakeSource({"AAA": series()})
    rates = FakeRateSource({"DGS3MO": published_rate()})

    dataset = await DataSteward(
        source,
        rate_source=rates,
        factor_source=FakeFactorSource(factor_frame()),
    ).resolve(spec(factors="ff3", risk_free="DGS3MO"))

    assert dataset.report.has("mixed_risk_free")
    assert dataset.report.flag("mixed_risk_free").severity == "warning"
    # The explicit rate wins; it is what the plan asked for.
    assert dataset.report.risk_free == "DGS3MO"
    assert dataset.frame["risk_free"].iloc[0] != pytest.approx(0.0001)


async def test_asking_for_factors_with_no_factor_source_is_refused():
    source = FakeSource({"AAA": series()})

    with pytest.raises(DataUnavailableError, match="factor"):
        await DataSteward(source).resolve(spec(factors="ff3"))


async def test_an_unavailable_factor_set_names_it():
    source = FakeSource({"AAA": series()})
    factors = FakeFactorSource(error=DataUnavailableError("carhart4 is monthly only"))

    with pytest.raises(DataUnavailableError, match="monthly only"):
        await DataSteward(source, factor_source=factors).resolve(spec(factors="carhart4"))


async def test_poor_factor_coverage_is_flagged_rather_than_silently_dropped():
    """Factors published on a US calendar against an asset trading elsewhere,
    or a window running past the library's last update, leaves rows a factor
    model will drop. The count belongs in the report, not in a surprise."""
    source = FakeSource({"AAA": series(periods=60)})
    # Factors cover only the first third of the price window.
    factors = FakeFactorSource(factor_frame(periods=20))

    dataset = await DataSteward(source, factor_source=factors).resolve(spec(factors="ff3"))

    assert dataset.report.has("factor_coverage")
    assert dataset.report.flag("factor_coverage").severity == "warning"


async def test_full_factor_coverage_raises_no_flag():
    source = FakeSource({"AAA": series(periods=60)})

    dataset = await DataSteward(
        source, factor_source=FakeFactorSource(factor_frame(periods=60))
    ).resolve(spec(factors="ff3"))

    assert not dataset.report.has("factor_coverage")


async def test_no_factor_columns_appear_when_none_were_asked_for():
    source = FakeSource({"AAA": series()})
    factors = FakeFactorSource(factor_frame())

    dataset = await DataSteward(source, factor_source=factors).resolve(spec())

    assert "mkt_rf" not in dataset.frame.columns
    assert factors.asked == []
    assert dataset.report.factors is None


async def test_factors_change_the_fingerprint():
    source = FakeSource({"AAA": series()})
    factors = FakeFactorSource(factor_frame())

    without = await DataSteward(source, factor_source=factors).resolve(spec())
    with_factors = await DataSteward(source, factor_source=factors).resolve(
        spec(factors="ff3")
    )

    assert without.report.fingerprint != with_factors.report.fingerprint


async def test_the_factor_frequency_follows_the_spec():
    source = FakeSource({"AAA": series(periods=400)})
    factors = FakeFactorSource(factor_frame(periods=400))

    await DataSteward(source, factor_source=factors, min_obs=3).resolve(
        spec(factors="ff3", frequency="M", end=date(2021, 1, 31))
    )

    assert factors.asked == [("ff3", "M")]
