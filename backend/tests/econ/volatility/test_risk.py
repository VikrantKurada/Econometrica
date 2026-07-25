"""Known-answer and property tests for the risk metric tools.

Sign convention under test everywhere: VaR and CVaR are POSITIVE loss
magnitudes — VaR95 = 0.023 means "5% chance of losing more than 2.3%". A
distribution whose tail quantile is still a gain yields a NEGATIVE VaR; that
is the convention working, not a bug, and is asserted explicitly.

The historical VaR quantile uses ``method='lower'``: the reported VaR is an
actual order statistic (on 1000 points at 95%, exactly the 50th smallest
return, negated), never an interpolation — which makes the known answer exact
and CVaR >= VaR an identity rather than an approximation.
"""

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import econometrica.econ.volatility  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_garch_series

RISK_TOOLS = ("historical_var", "parametric_var", "cvar", "ewma_vol", "realized_vol", "drawdown")


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


def normal_returns(n: int = 1000, seed: int = 1, sigma: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-02", periods=n)
    return pd.DataFrame({"return": rng.normal(0.0, sigma, n)}, index=idx)


def iid_t_returns(n: int = 3000, seed: int = 3) -> pd.DataFrame:
    """alpha = beta = 0 collapses the GARCH fixture to i.i.d. scaled t(6) draws."""
    series = make_garch_series(omega=1e-4, alpha=0.0, beta=0.0, n=n, seed=seed, dist="t", nu=6.0)
    return pd.DataFrame({"return": series})


def price_frame(prices: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2015-01-02", periods=len(prices))
    return pd.DataFrame({"price": prices}, index=idx)


def test_risk_tools_are_registered_in_the_volatility_family():
    for name in RISK_TOOLS:
        tool = get_registry().get(name)
        assert tool.family == "volatility"
        assert tool.version


def test_var_summaries_document_the_positive_loss_convention():
    for name in ("historical_var", "parametric_var", "cvar"):
        summary = get_registry().get(name).summary.lower()
        assert "positive loss" in summary


# ---------------------------------------------------------------- historical_var


def test_historical_var_is_the_50th_smallest_return_negated_on_1000_points():
    """The plan's exact known answer, guaranteed by the 'lower' quantile method."""
    frame = normal_returns(n=1000)
    result = run_tool("historical_var", frame)
    expected = -float(np.sort(frame["return"].to_numpy())[49])
    assert result.scalars["var"] == expected
    assert result.scalars["var"] > 0
    assert result.scalars["nobs"] == 1000.0


def test_historical_var_is_negative_when_even_the_tail_is_a_gain():
    """Positive-loss convention: an always-profitable series has VaR < 0."""
    frame = normal_returns(n=500, seed=2, sigma=0.0001) + 0.05
    result = run_tool("historical_var", frame)
    assert result.scalars["var"] < 0


def test_historical_var_scales_with_the_returns():
    frame = normal_returns(n=500, seed=3)
    base = run_tool("historical_var", frame).scalars["var"]
    doubled = run_tool("historical_var", frame * 2.0).scalars["var"]
    assert doubled == pytest.approx(2.0 * base)


def test_historical_var_respects_the_confidence_level():
    frame = normal_returns(n=1000, seed=4)
    var95 = run_tool("historical_var", frame, confidence=0.95).scalars["var"]
    var99 = run_tool("historical_var", frame, confidence=0.99).scalars["var"]
    assert var99 > var95


# ---------------------------------------------------------------- parametric_var


def test_parametric_var_normal_matches_the_closed_form():
    from scipy import stats

    frame = normal_returns(n=2000, seed=5)
    result = run_tool("parametric_var", frame)
    values = frame["return"].to_numpy()
    mu, sigma = float(values.mean()), float(values.std(ddof=1))
    expected = -(mu + sigma * float(stats.norm.ppf(0.05)))
    assert result.scalars["var"] == pytest.approx(expected)
    assert result.scalars["mu"] == pytest.approx(mu)
    assert result.scalars["sigma"] == pytest.approx(sigma)
    # and the estimate is close to the truth: 1.645 * 0.01
    assert result.scalars["var"] == pytest.approx(0.01645, rel=0.1)


def test_parametric_var_zero_mean_uses_the_root_mean_square():
    from scipy import stats

    frame = normal_returns(n=2000, seed=5)
    result = run_tool("parametric_var", frame, zero_mean=True)
    values = frame["return"].to_numpy()
    rms = float(np.sqrt(np.mean(values**2)))
    assert result.scalars["var"] == pytest.approx(-rms * float(stats.norm.ppf(0.05)))
    assert result.scalars["mu"] == 0.0


def test_parametric_var_t_recovers_nu_on_fat_tailed_data():
    result = run_tool("parametric_var", iid_t_returns(), dist="t")
    assert result.scalars["nu"] == pytest.approx(6.0, abs=2.0)
    assert result.scalars["var"] > 0


def test_parametric_var_t_exceeds_normal_in_the_far_tail_of_fat_tailed_data():
    frame = iid_t_returns()
    var_t = run_tool("parametric_var", frame, dist="t", confidence=0.99).scalars["var"]
    var_n = run_tool("parametric_var", frame, dist="normal", confidence=0.99).scalars["var"]
    assert var_t > var_n


# ---------------------------------------------------------------------- cvar


def test_cvar_is_the_mean_of_the_50_worst_returns_negated_on_1000_points():
    frame = normal_returns(n=1000, seed=6)
    result = run_tool("cvar", frame)
    worst_50 = np.sort(frame["return"].to_numpy())[:50]
    assert result.scalars["cvar"] == pytest.approx(-float(worst_50.mean()))
    assert result.scalars["n_tail"] == 50.0
    assert result.scalars["cvar"] > result.scalars["var"]


@given(
    returns=st.lists(
        st.floats(min_value=-0.5, max_value=0.5, allow_nan=False), min_size=25, max_size=120
    ),
    confidence=st.floats(min_value=0.55, max_value=0.99),
)
@settings(max_examples=60, deadline=None)
def test_cvar_is_never_below_var(returns: list[float], confidence: float):
    """The plan's property: as positive losses, CVaR >= VaR for ANY sample.

    Exact under the 'lower' quantile convention: the tail mean averages values
    at or below the VaR order statistic.
    """
    frame = pd.DataFrame({"return": returns})
    result = run_tool("cvar", frame, confidence=confidence, min_obs=20)
    assert result.scalars["cvar"] >= result.scalars["var"] - 1e-12


# ------------------------------------------------------------------- ewma_vol


def test_ewma_vol_matches_the_hand_computed_three_step_recursion():
    """sigma2_1 = r_1^2; sigma2_t = 0.94 sigma2_{t-1} + 0.06 r_t^2."""
    frame = pd.DataFrame(
        {"return": [0.01, -0.02, 0.015]}, index=pd.bdate_range("2015-01-02", periods=3)
    )
    result = run_tool("ewma_vol", frame, min_obs=3)
    vols = result.series["ewma_vol"].y
    s1 = 0.01**2
    s2 = 0.94 * s1 + 0.06 * 0.02**2
    s3 = 0.94 * s2 + 0.06 * 0.015**2
    assert vols[0] == pytest.approx(math.sqrt(s1))
    assert vols[1] == pytest.approx(math.sqrt(s2))
    assert vols[2] == pytest.approx(math.sqrt(s3))
    assert result.scalars["current_vol"] == pytest.approx(math.sqrt(s3))
    assert result.scalars["current_vol_annualized"] == pytest.approx(
        math.sqrt(s3) * math.sqrt(252.0)
    )


def test_ewma_vol_of_a_constant_return_series_is_that_magnitude_exactly():
    """With sigma2_1 = r_1^2 the recursion has fixed point |c| at every step."""
    frame = pd.DataFrame(
        {"return": [-0.02] * 50}, index=pd.bdate_range("2015-01-02", periods=50)
    )
    result = run_tool("ewma_vol", frame)
    assert all(v == pytest.approx(0.02) for v in result.series["ewma_vol"].y)


@given(scale=st.floats(min_value=0.01, max_value=100.0))
@settings(max_examples=30, deadline=None)
def test_ewma_vol_is_scale_equivariant(scale: float):
    frame = normal_returns(n=100, seed=7)
    base = run_tool("ewma_vol", frame).series["ewma_vol"].y
    scaled = run_tool("ewma_vol", frame * scale).series["ewma_vol"].y
    for b, s in zip(base, scaled, strict=True):
        assert b is not None and s is not None
        assert s == pytest.approx(scale * b, rel=1e-9)


def test_ewma_lambda_bounds_are_enforced():
    with pytest.raises(ValueError):
        run_tool("ewma_vol", normal_returns(n=100), lambda_=1.0)


# --------------------------------------------------------------- realized_vol


def test_realized_vol_matches_the_manual_rolling_root_mean_square():
    frame = normal_returns(n=300, seed=8)
    window = 21
    result = run_tool("realized_vol", frame, window=window)
    values = frame["return"].to_numpy()
    y = result.series["realized_vol"].y
    assert all(v is None for v in y[: window - 1])
    expected_last = math.sqrt(float(np.mean(values[-window:] ** 2)))
    assert y[-1] == pytest.approx(expected_last)
    annualized = result.series["realized_vol_annualized"].y
    assert annualized[-1] == pytest.approx(expected_last * math.sqrt(252.0))
    assert result.scalars["current_vol"] == pytest.approx(expected_last)


def test_realized_vol_demeaned_uses_the_rolling_sample_std():
    frame = normal_returns(n=300, seed=8)
    result = run_tool("realized_vol", frame, window=21, demean=True)
    expected_last = float(frame["return"].iloc[-21:].std(ddof=1))
    assert result.series["realized_vol"].y[-1] == pytest.approx(expected_last)


def test_realized_vol_is_scale_equivariant():
    frame = normal_returns(n=200, seed=9)
    base = run_tool("realized_vol", frame).scalars["current_vol"]
    tripled = run_tool("realized_vol", frame * 3.0).scalars["current_vol"]
    assert tripled == pytest.approx(3.0 * base)


def test_realized_vol_window_larger_than_the_sample_raises():
    with pytest.raises(ValueError, match="window"):
        run_tool("realized_vol", normal_returns(n=100), window=150)


# ------------------------------------------------------------------- drawdown


def test_drawdown_known_answer_on_the_hand_built_path():
    """The plan's exact case: [100, 120, 90, 95, 130] -> 25% (120 -> 90)."""
    frame = price_frame([100.0, 120.0, 90.0, 95.0, 130.0])
    result = run_tool("drawdown", frame)
    assert result.scalars["max_drawdown"] == 0.25
    table = result.tables["max_drawdown"]
    assert table.columns == ["start", "trough", "end"]
    (row,) = table.rows
    dates = [d.strftime("%Y-%m-%d") for d in frame.index]
    assert row == [dates[1], dates[2], dates[4]]  # peak 120, trough 90, recovery 130
    # peak 2015-01-05 (Mon) to recovery 2015-01-08 (Thu): 3 calendar days
    assert result.scalars["longest_underwater_days"] == 3.0
    assert result.scalars["current_drawdown"] == 0.0
    assert result.scalars["n_drawdowns"] == 1.0


def test_drawdown_series_is_positive_magnitudes_anchored_at_zero():
    result = run_tool("drawdown", price_frame([100.0, 120.0, 90.0, 95.0, 130.0]))
    y = result.series["drawdown"].y
    assert y[0] == 0.0 and y[1] == 0.0  # at the running max
    assert y[2] == pytest.approx(0.25)
    assert y[3] == pytest.approx(1.0 - 95.0 / 120.0)
    assert y[4] == 0.0


def test_drawdown_on_a_monotone_path_is_zero_with_no_episodes():
    result = run_tool("drawdown", price_frame([100.0, 101.0, 103.0, 110.0]))
    assert result.scalars["max_drawdown"] == 0.0
    assert result.scalars["longest_underwater_days"] == 0.0
    assert result.scalars["n_drawdowns"] == 0.0
    assert result.tables["max_drawdown"].rows == []


def test_drawdown_unrecovered_episode_has_no_end_date_and_counts_to_the_last_obs():
    frame = price_frame([100.0, 120.0, 90.0, 95.0])
    result = run_tool("drawdown", frame)
    assert result.scalars["max_drawdown"] == 0.25
    (row,) = result.tables["max_drawdown"].rows
    assert row[2] is None  # never recovered
    # peak 2015-01-05 to the last observation 2015-01-07: 2 calendar days, ongoing
    assert result.scalars["longest_underwater_days"] == 2.0
    assert result.scalars["current_drawdown"] == pytest.approx(1.0 - 95.0 / 120.0)


def test_drawdown_accepts_returns_input_and_agrees_with_the_price_path():
    prices = price_frame([100.0, 120.0, 90.0, 95.0, 130.0])
    returns = prices["price"].pct_change().dropna().to_frame(name="return")
    from_prices = run_tool("drawdown", prices)
    from_returns = run_tool(
        "drawdown", returns, column="return", input_type="returns", min_obs=4
    )
    assert from_returns.scalars["max_drawdown"] == pytest.approx(
        from_prices.scalars["max_drawdown"]
    )


def test_drawdown_rejects_nonpositive_prices():
    with pytest.raises(ValueError, match="positive"):
        run_tool("drawdown", price_frame([100.0, -5.0, 90.0]), min_obs=3)


def test_drawdown_rejects_returns_at_or_below_minus_one():
    frame = pd.DataFrame(
        {"return": [0.01, -1.0, 0.02]}, index=pd.bdate_range("2015-01-02", periods=3)
    )
    with pytest.raises(ValueError, match="-1"):
        run_tool("drawdown", frame, column="return", input_type="returns", min_obs=3)


# ------------------------------------------------------------------- plumbing


@pytest.mark.parametrize("name", RISK_TOOLS)
def test_missing_column_raises_an_error_naming_the_tool(name: str):
    frame = normal_returns(n=300).rename(columns={"return": "other"})
    with pytest.raises(ValueError, match=name):
        run_tool(name, frame)


@pytest.mark.parametrize("name", ("historical_var", "parametric_var", "cvar"))
def test_too_few_observations_raises_an_actionable_error(name: str):
    with pytest.raises(ValueError, match="observations"):
        run_tool(name, normal_returns(n=10))


def test_manifests_record_the_tool():
    frame = normal_returns(n=300)
    result = run_tool("historical_var", frame)
    assert result.manifest.tool == "historical_var"
    assert "numpy" in result.manifest.library_versions
