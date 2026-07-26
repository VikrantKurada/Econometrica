"""FRED series, and the risk-free rate conventions built on them.

FRED publishes *series*, not prices: `SP500` is an index level, `DGS3MO` is a
yield in percent per annum. The adapter returns what was published and converts
nothing, because it cannot know what an arbitrary series id means. Interpreting
one as a risk-free rate is `data/rates.py`'s job, and it refuses series it has
no convention for rather than guessing at a scale.
"""

import threading
from datetime import date

import pandas as pd
import pytest

from econometrica.agents.data_steward import DataUnavailableError
from econometrica.data.fred import FredSeriesSource
from econometrica.data.rates import CONVENTIONS, resolve_rate, to_period_rate

START, END = date(2024, 1, 2), date(2024, 1, 20)


class FakeReader:
    """Stands in for `pandas_datareader.data.DataReader`."""

    def __init__(self, frame: pd.DataFrame | None) -> None:
        self.frame = frame
        self.calls: list[dict[str, object]] = []
        self.thread_ids: list[int] = []

    def __call__(self, name: str, data_source: str, **kwargs: object) -> pd.DataFrame | None:
        self.calls.append({"name": name, "data_source": data_source, **kwargs})
        self.thread_ids.append(threading.get_ident())
        return self.frame


def frame(
    series_id: str = "DGS3MO",
    values: list[float] | None = None,
    index: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """A FRED-shaped frame: one column named for the series, index named DATE."""
    values = values if values is not None else [5.46, 5.48, 5.48]
    if index is None:
        index = pd.DatetimeIndex(pd.date_range("2024-01-02", periods=len(values), freq="D"))
    built = pd.DataFrame({series_id: values}, index=index)
    built.index.name = "DATE"
    return built


async def fetch(reader: FakeReader, series_id: str = "DGS3MO") -> pd.Series:
    return await FredSeriesSource(reader=reader).prices(series_id, start=START, end=END)


# --- the adapter -------------------------------------------------------------


async def test_it_returns_the_published_series_named_for_its_id():
    reader = FakeReader(frame())

    series = await fetch(reader)

    assert series.name == "DGS3MO"
    assert list(series) == pytest.approx([5.46, 5.48, 5.48])
    assert isinstance(series.index, pd.DatetimeIndex)


async def test_it_converts_nothing():
    """The adapter cannot know whether a series id is a rate in percent or an
    index level, so it must not scale anything. `data/rates.py` does that where
    the convention is declared."""
    reader = FakeReader(frame(values=[5.46]))

    series = await fetch(reader)

    assert series.iloc[0] == pytest.approx(5.46)


async def test_it_asks_fred():
    reader = FakeReader(frame())

    await fetch(reader)

    assert reader.calls[0]["data_source"] == "fred"
    assert reader.calls[0]["name"] == "DGS3MO"


async def test_an_empty_result_is_refused_with_the_series_named():
    reader = FakeReader(pd.DataFrame())

    with pytest.raises(DataUnavailableError, match="NOTASERIES"):
        await fetch(reader, "NOTASERIES")


async def test_a_none_result_is_refused():
    reader = FakeReader(None)

    with pytest.raises(DataUnavailableError, match="NOTASERIES"):
        await fetch(reader, "NOTASERIES")


async def test_gaps_are_dropped():
    """FRED publishes a missing observation as `.`, which arrives as NaN — a
    treasury series has one on every market holiday."""
    reader = FakeReader(frame(values=[5.46, float("nan"), 5.48]))

    series = await fetch(reader)

    assert len(series) == 2


async def test_a_series_of_nothing_but_gaps_is_refused():
    reader = FakeReader(frame(values=[float("nan")] * 3))

    with pytest.raises(DataUnavailableError, match="DGS3MO"):
        await fetch(reader)


async def test_a_backwards_window_is_refused_before_any_fetch():
    reader = FakeReader(frame())

    with pytest.raises(ValueError, match="window"):
        await FredSeriesSource(reader=reader).prices("DGS3MO", start=END, end=START)

    assert reader.calls == []


async def test_the_blocking_read_runs_off_the_event_loop():
    """pandas-datareader is synchronous and fetches over HTTP."""
    reader = FakeReader(frame())

    await fetch(reader)

    assert reader.thread_ids[0] != threading.get_ident()


async def test_the_label_names_fred_and_not_synthetic():
    label = FredSeriesSource().label.lower()

    assert "fred" in label
    assert "synthetic" not in label


# --- rate conventions --------------------------------------------------------


def test_an_annualised_percent_rate_becomes_a_per_period_decimal():
    published = pd.Series([5.46], index=pd.to_datetime(["2024-01-02"]))

    daily = to_period_rate(published, series_id="DGS3MO", periods_per_year=252)

    assert daily.iloc[0] == pytest.approx(1.0546 ** (1 / 252) - 1, rel=1e-12)


def test_the_conversion_compounds_rather_than_dividing():
    """`5.46/100/252` and `(1.0546)**(1/252)-1` differ in the fourth
    significant digit, and both appear in published work. The compounding form
    is chosen to match Ken French, whose own file description defines RF as
    "the simple daily rate that, over the number of trading days in the month,
    compounds to" the monthly bill rate."""
    published = pd.Series([5.46], index=pd.to_datetime(["2024-01-02"]))

    daily = to_period_rate(published, series_id="DGS3MO", periods_per_year=252)

    naive = 5.46 / 100 / 252
    assert daily.iloc[0] != pytest.approx(naive, rel=1e-4)
    assert daily.iloc[0] < naive


def test_the_period_count_follows_the_frequency():
    published = pd.Series([5.46], index=pd.to_datetime(["2024-01-31"]))

    monthly = to_period_rate(published, series_id="DGS3MO", periods_per_year=12)

    assert monthly.iloc[0] == pytest.approx(1.0546 ** (1 / 12) - 1, rel=1e-12)


def test_a_zero_rate_stays_zero():
    published = pd.Series([0.0], index=pd.to_datetime(["2021-01-04"]))

    assert to_period_rate(published, series_id="DGS3MO", periods_per_year=252).iloc[0] == 0.0


def test_a_negative_rate_survives():
    """Policy rates have been below zero, and a conversion that quietly
    clamped would misstate a whole decade of European work."""
    published = pd.Series([-0.5], index=pd.to_datetime(["2020-01-02"]))

    converted = to_period_rate(published, series_id="DGS3MO", periods_per_year=252)

    assert converted.iloc[0] < 0


def test_a_series_with_no_declared_convention_is_refused():
    """Guessing a scale from magnitude is how a rate becomes an index level.
    An unlisted id raises, and the message names what is known so the fix is
    one entry in one table."""
    published = pd.Series([4742.83], index=pd.to_datetime(["2024-01-02"]))

    with pytest.raises(DataUnavailableError, match="SP500"):
        to_period_rate(published, series_id="SP500", periods_per_year=252)


def test_the_known_conventions_are_all_annualised_percentages():
    """Every entry so far is a FRED rate published in percent per annum. This
    holds the table honest when Ken French's RF is added in 6.4 — that one is
    already per-period, so it must arrive with `annualised=False` rather than
    being folded in here."""
    for series_id, convention in CONVENTIONS.items():
        assert convention.scale == 100.0, series_id
        assert convention.description, series_id


async def test_resolve_rate_fetches_through_the_given_source_and_converts():
    reader = FakeReader(frame(values=[5.46, 5.48]))
    source = FredSeriesSource(reader=reader)

    rate = await resolve_rate(
        source, "DGS3MO", start=START, end=END, periods_per_year=252
    )

    assert rate.iloc[0] == pytest.approx(1.0546 ** (1 / 252) - 1, rel=1e-12)
    assert rate.name == "DGS3MO"


# --- live -------------------------------------------------------------------


def _fred_is_reachable() -> bool:
    import httpx

    try:
        httpx.get("https://fred.stlouisfed.org/", timeout=5.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_a_real_series_resolves():
    """No API key, which is why FRED is the second source rather than a keyed
    vendor. The index arrives as `datetime64[us]` named DATE."""
    if not _fred_is_reachable():
        pytest.skip("fred is not reachable")

    series = await FredSeriesSource().prices("DGS3MO", start=START, end=END)

    assert len(series) >= 5
    assert series.name == "DGS3MO"
    assert isinstance(series.index, pd.DatetimeIndex)
    # A 3-month yield in percent per annum. If this ever looks like 0.05, the
    # adapter has started converting and every excess return is wrong by 100x.
    assert 0.0 <= series.max() <= 30.0


@pytest.mark.live
async def test_live_fred_and_yahoo_agree_on_the_same_index():
    """The cross-check that replaces Stooq. Two independent pipelines for one
    series is the whole reason `DataQualityReport.source` is worth recording —
    and if they ever stop agreeing, that is a finding rather than a flake."""
    if not _fred_is_reachable():
        pytest.skip("fred is not reachable")

    from econometrica.data.yahoo import YahooPriceSource

    window = {"start": date(2024, 1, 2), "end": date(2024, 1, 12)}
    fred = await FredSeriesSource().prices("SP500", **window)
    yahoo = await YahooPriceSource().prices("^GSPC", **window)

    shared = fred.index.intersection(yahoo.index)
    assert len(shared) >= 5
    # Yahoo carries more decimals; agreement to a tenth of an index point is
    # the same number.
    assert (fred.loc[shared] - yahoo.loc[shared]).abs().max() < 0.1
