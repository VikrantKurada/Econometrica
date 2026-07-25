"""Known-answer tests for the rolling beta tool.

The canonical case: a hard beta break from 0.8 to 1.6 at the sample midpoint
must show up as ~0.8 well before the break, ~1.6 well after, and a crossing
of the midpoint value in between.
"""

import pandas as pd
import pytest

import econometrica.econ.pricing  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_capm_data

WINDOW = 252
N_HALF = 1000


def run_rolling(data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get("rolling_beta")
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def break_data() -> pd.DataFrame:
    """Beta 0.8 for the first 1000 days, 1.6 for the next 1000."""
    first = make_capm_data(beta=0.8, alpha=0.0, n=N_HALF, seed=1, resid_vol=0.005)
    second = make_capm_data(beta=1.6, alpha=0.0, n=N_HALF, seed=2, resid_vol=0.005)
    data = pd.concat([first, second], ignore_index=True)
    data.index = pd.bdate_range("2015-01-02", periods=2 * N_HALF)
    return data


def test_rolling_beta_is_registered_in_the_pricing_family():
    assert get_registry().get("rolling_beta").family == "pricing"


def test_rolling_beta_tracks_a_known_beta_break(break_data: pd.DataFrame):
    result = run_rolling(break_data, window=WINDOW)
    beta = result.series["beta"]

    # One estimate per full window: first at position WINDOW - 1.
    assert len(beta.y) == 2 * N_HALF - WINDOW + 1

    def at(window_end: int) -> float:
        value = beta.y[window_end - (WINDOW - 1)]
        assert value is not None
        return value

    assert at(900) == pytest.approx(0.8, abs=0.1)  # window fully pre-break
    assert at(1500) == pytest.approx(1.6, abs=0.1)  # window fully post-break

    # The estimate crosses the midpoint 1.2 roughly where the window straddles
    # the break half-and-half (window end ~ 1000 + WINDOW/2).
    crossing = next(
        i for i, value in enumerate(beta.y) if value is not None and value >= 1.2
    ) + (WINDOW - 1)
    assert 1000 < crossing < 1000 + WINDOW


def test_rolling_beta_confidence_band_brackets_the_estimate(break_data: pd.DataFrame):
    result = run_rolling(break_data, window=WINDOW)
    beta = result.series["beta"]
    low = result.series["beta_ci_low"]
    high = result.series["beta_ci_high"]

    assert len(low.y) == len(beta.y) == len(high.y)
    assert low.x == beta.x == high.x
    for lo, mid, hi in zip(low.y, beta.y, high.y, strict=True):
        assert lo is not None and mid is not None and hi is not None
        assert lo < mid < hi


def test_rolling_beta_dates_are_iso_strings(break_data: pd.DataFrame):
    result = run_rolling(break_data, window=WINDOW)
    beta = result.series["beta"]
    # First full window ends WINDOW - 1 business days after the start.
    expected_first = pd.bdate_range("2015-01-02", periods=WINDOW)[-1].strftime("%Y-%m-%d")
    assert beta.x[0] == expected_first
    assert all(isinstance(x, str) for x in beta.x)


def test_rolling_beta_scalars_summarise_the_path(break_data: pd.DataFrame):
    result = run_rolling(break_data, window=WINDOW)
    assert result.scalars["window"] == WINDOW
    assert result.scalars["nobs"] == 2 * N_HALF
    assert result.scalars["beta_min"] == pytest.approx(0.8, abs=0.15)
    assert result.scalars["beta_max"] == pytest.approx(1.6, abs=0.15)
    assert 0.8 < result.scalars["beta_mean"] < 1.6


def test_rolling_beta_is_stable_when_the_true_beta_is_constant():
    data = make_capm_data(beta=1.3, alpha=0.0, n=1500, seed=9, resid_vol=0.005)
    result = run_rolling(data, window=WINDOW)
    values = [v for v in result.series["beta"].y if v is not None]
    assert all(v == pytest.approx(1.3, abs=0.2) for v in values)


def test_window_larger_than_the_sample_raises(break_data: pd.DataFrame):
    with pytest.raises(ValueError, match="window"):
        run_rolling(break_data.iloc[:200], window=WINDOW)


def test_rolling_beta_manifest_is_populated(break_data: pd.DataFrame):
    result = run_rolling(break_data, window=WINDOW)
    assert result.manifest.tool == "rolling_beta"
    assert "statsmodels" in result.manifest.library_versions
    assert result.manifest.params_hash
