"""The synthetic price source.

It exists so the pipeline can run before Phase 6 ships real market data. That
makes honesty its main design constraint: it must be reproducible, it must
behave like the thing it stands in for, and it must never be mistaken for
real prices.
"""

from datetime import date

import pytest
from statsmodels.tsa.stattools import adfuller

from econometrica.data.synthetic import SyntheticPriceSource

START, END = date(2020, 1, 1), date(2023, 12, 31)


async def prices(ticker: str = "BTC-USD", **kwargs):
    source = SyntheticPriceSource(**kwargs)
    return await source.prices(ticker, start=START, end=END)


async def test_the_same_ticker_and_window_give_the_same_series():
    """A reproducibility manifest is worthless over data that moves."""
    first = await prices()
    second = await prices()

    assert first.equals(second)


async def test_different_tickers_give_different_series():
    btc = await prices("BTC-USD")
    eth = await prices("ETH-USD")

    assert not btc.equals(eth)


async def test_the_series_covers_the_requested_window():
    series = await prices()

    assert series.index.min().date() >= START
    assert series.index.max().date() <= END


async def test_prices_are_strictly_positive():
    """Log transforms refuse non-positive values, and half the registry logs."""
    series = await prices()

    assert (series > 0).all()


async def test_it_behaves_like_the_random_walk_it_claims_to_be():
    """Otherwise a test that asks whether prices follow a random walk is
    proving something about the generator's bugs, not about the pipeline."""
    series = await prices()

    assert adfuller(series)[1] > 0.10


async def test_a_seed_change_changes_the_path():
    assert not (await prices(seed=1)).equals(await prices(seed=2))


async def test_it_announces_itself_as_synthetic():
    """Nothing downstream should have to guess where the numbers came from."""
    assert "synthetic" in SyntheticPriceSource().label.lower()


async def test_an_empty_window_is_refused_rather_than_returned_empty():
    source = SyntheticPriceSource()

    with pytest.raises(ValueError, match="window"):
        await source.prices("BTC-USD", start=END, end=START)
