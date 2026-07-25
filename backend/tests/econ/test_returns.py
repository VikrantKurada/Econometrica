import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from econometrica.econ.returns import (
    align_series,
    annualise_return,
    excess_returns,
    to_returns,
)


def test_simple_returns_computed_correctly():
    prices = pd.Series([100.0, 110.0, 99.0])
    result = to_returns(prices, method="simple")
    assert result.iloc[0] == pytest.approx(0.10)
    assert result.iloc[1] == pytest.approx(-0.10)


def test_log_returns_computed_correctly():
    prices = pd.Series([100.0, 110.0])
    result = to_returns(prices, method="log")
    assert result.iloc[0] == pytest.approx(np.log(1.1))


def test_returns_drop_the_first_observation():
    prices = pd.Series([100.0, 110.0, 120.0])
    assert len(to_returns(prices, method="simple")) == 2


def test_log_returns_are_additive_over_time():
    """The defining property of log returns — worth asserting, not assuming."""
    prices = pd.Series([100.0, 110.0, 121.0, 108.9])
    log_ret = to_returns(prices, method="log")
    total = np.log(prices.iloc[-1] / prices.iloc[0])
    assert log_ret.sum() == pytest.approx(total)


@given(st.floats(min_value=1.0, max_value=1000.0, allow_nan=False))
@settings(max_examples=50)
def test_flat_prices_produce_zero_returns(price):
    prices = pd.Series([price] * 5)
    assert to_returns(prices, method="simple").abs().max() == pytest.approx(0.0)


def test_align_series_keeps_only_shared_dates():
    idx_a = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
    idx_b = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    a = pd.Series([1.0, 2.0, 3.0], index=idx_a, name="a")
    b = pd.Series([4.0, 5.0, 6.0], index=idx_b, name="b")

    aligned = align_series({"a": a, "b": b})

    assert list(aligned.index) == list(pd.to_datetime(["2020-01-02", "2020-01-03"]))
    assert list(aligned.columns) == ["a", "b"]


def test_align_series_raises_when_no_overlap():
    a = pd.Series([1.0], index=pd.to_datetime(["2020-01-01"]))
    b = pd.Series([1.0], index=pd.to_datetime(["2021-01-01"]))
    with pytest.raises(ValueError, match="no overlapping"):
        align_series({"a": a, "b": b})


def test_excess_returns_subtract_the_risk_free_rate():
    idx = pd.to_datetime(["2020-01-01", "2020-01-02"])
    asset = pd.Series([0.01, 0.02], index=idx)
    rf = pd.Series([0.001, 0.001], index=idx)
    result = excess_returns(asset, rf)
    assert result.iloc[0] == pytest.approx(0.009)


def test_annualise_return_uses_the_stated_frequency():
    assert annualise_return(0.0005, periods_per_year=252) == pytest.approx(
        (1.0005) ** 252 - 1
    )
