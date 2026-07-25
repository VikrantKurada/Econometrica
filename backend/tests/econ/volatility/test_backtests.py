"""Known-answer tests for the VaR backtests (Kupiec and Christoffersen).

The three-way contrast is the point of the pair:

- a CORRECT 95% VaR on i.i.d. returns passes both tests;
- a VaR computed at the WRONG quantile fails Kupiec (coverage);
- a static VaR on GARCH returns has the RIGHT unconditional coverage but
  clustered violations — Kupiec passes while Christoffersen's independence
  test rejects. Only the pair together sees that failure mode.

Conventions under test: VaR inputs are POSITIVE loss magnitudes; a violation
is a return strictly below -VaR (a return exactly at the boundary is not a
violation); ``passed=True`` means the test does NOT reject — the VaR looks
well calibrated.
"""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

import econometrica.econ.volatility  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_garch_series

SIGMA = 0.01
Z95 = float(stats.norm.ppf(0.95))
Z80 = float(stats.norm.ppf(0.80))


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


def normal_frame(n: int = 1000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-02", periods=n)
    return pd.DataFrame({"return": rng.normal(0.0, SIGMA, n)}, index=idx)


def clustered_frame(n: int = 3000, seed: int = 9) -> tuple[pd.DataFrame, float]:
    """GARCH returns with a static VaR at the empirical 5% quantile.

    Unconditional coverage is correct BY CONSTRUCTION (the quantile is taken
    on the sample itself), but volatility clustering makes the violations
    arrive in runs during high-variance episodes.
    """
    series = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=n, seed=seed)
    static_var = -float(np.quantile(series.to_numpy(), 0.05, method="lower"))
    return pd.DataFrame({"return": series}), static_var


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


def test_backtests_are_registered_in_the_volatility_family():
    for name in ("kupiec_test", "christoffersen_test"):
        tool = get_registry().get(name)
        assert tool.family == "volatility"
        assert tool.version


def test_kupiec_passes_a_correctly_calibrated_var():
    result = run_tool("kupiec_test", normal_frame(), var_level=Z95 * SIGMA)
    diag = diagnostic(result, "kupiec")
    assert diag.p_value is not None and diag.p_value > 0.05
    assert diag.passed is True
    assert result.scalars["expected_violations"] == pytest.approx(50.0)
    # coverage should be in the right neighbourhood on 1000 draws
    assert 30 <= result.scalars["violations"] <= 70


def test_kupiec_rejects_a_var_computed_at_the_wrong_quantile():
    """An 80% VaR used as if it were 95%: far too many violations."""
    result = run_tool("kupiec_test", normal_frame(), var_level=Z80 * SIGMA)
    diag = diagnostic(result, "kupiec")
    assert diag.p_value is not None and diag.p_value < 0.01
    assert diag.passed is False
    assert result.scalars["violations"] > 3 * result.scalars["expected_violations"]


def test_kupiec_violation_count_matches_a_manual_count():
    frame = normal_frame(seed=11)
    var_level = Z95 * SIGMA
    result = run_tool("kupiec_test", frame, var_level=var_level)
    manual = int((frame["return"].to_numpy() < -var_level).sum())
    assert result.scalars["violations"] == float(manual)
    assert result.scalars["violation_rate"] == pytest.approx(manual / 1000.0)
    assert result.scalars["nobs"] == 1000.0


def test_a_return_exactly_at_the_var_boundary_is_not_a_violation():
    idx = pd.bdate_range("2015-01-02", periods=100)
    values = [-0.01, 0.005] * 50
    frame = pd.DataFrame({"return": values}, index=idx)
    result = run_tool("kupiec_test", frame, var_level=0.01, min_obs=100)
    assert result.scalars["violations"] == 0.0


def test_kupiec_rejects_an_overly_conservative_var_with_zero_violations():
    """Zero violations against an expected 50 is also miscalibration."""
    result = run_tool("kupiec_test", normal_frame(), var_level=10.0 * SIGMA)
    diag = diagnostic(result, "kupiec")
    assert result.scalars["violations"] == 0.0
    assert np.isfinite(diag.statistic)
    assert diag.p_value is not None and diag.p_value < 0.01
    assert diag.passed is False


def test_christoffersen_passes_a_correctly_calibrated_var_on_iid_returns():
    result = run_tool("christoffersen_test", normal_frame(), var_level=Z95 * SIGMA)
    independence = diagnostic(result, "christoffersen_independence")
    conditional = diagnostic(result, "christoffersen_conditional_coverage")
    for diag in (independence, conditional):
        assert diag.p_value is not None and diag.p_value > 0.05
        assert diag.passed is True


def test_clustered_violations_fail_christoffersen_while_passing_kupiec():
    """The reason both tools exist: coverage can be right while timing is wrong."""
    frame, static_var = clustered_frame()
    kupiec = diagnostic(
        run_tool("kupiec_test", frame, var_level=static_var), "kupiec"
    )
    chris = run_tool("christoffersen_test", frame, var_level=static_var)
    independence = diagnostic(chris, "christoffersen_independence")

    assert kupiec.p_value is not None and kupiec.p_value > 0.05
    assert kupiec.passed is True
    assert independence.p_value is not None and independence.p_value < 0.01
    assert independence.passed is False


def test_christoffersen_conditional_coverage_is_uc_plus_independence():
    frame, static_var = clustered_frame()
    result = run_tool("christoffersen_test", frame, var_level=static_var)
    independence = diagnostic(result, "christoffersen_independence")
    conditional = diagnostic(result, "christoffersen_conditional_coverage")
    assert conditional.statistic == pytest.approx(
        independence.statistic + result.scalars["lr_unconditional"]
    )


def test_christoffersen_reports_the_transition_counts():
    frame, static_var = clustered_frame()
    result = run_tool("christoffersen_test", frame, var_level=static_var)
    counts = [result.scalars[k] for k in ("n00", "n01", "n10", "n11")]
    assert sum(counts) == result.scalars["nobs"] - 1
    assert result.scalars["n11"] > 0  # the clustering the test detects


def test_christoffersen_with_no_violations_is_not_judged_for_independence():
    """The degenerate case must be documented behaviour, not a crash."""
    result = run_tool("christoffersen_test", normal_frame(), var_level=10.0 * SIGMA)
    independence = diagnostic(result, "christoffersen_independence")
    conditional = diagnostic(result, "christoffersen_conditional_coverage")
    assert result.scalars["violations"] == 0.0
    assert independence.passed is None
    assert conditional.passed is None
    assert "no violations" in independence.interpretation.lower()


def test_var_column_and_static_level_agree_when_the_column_is_constant():
    frame = normal_frame(seed=13)
    var_level = Z95 * SIGMA
    frame_with_column = frame.assign(var_forecast=var_level)
    from_level = run_tool("kupiec_test", frame, var_level=var_level)
    from_column = run_tool("kupiec_test", frame_with_column, var_column="var_forecast")
    assert from_level.scalars["violations"] == from_column.scalars["violations"]
    assert diagnostic(from_level, "kupiec").statistic == pytest.approx(
        diagnostic(from_column, "kupiec").statistic
    )


def test_var_column_rows_with_missing_forecasts_are_dropped():
    frame = normal_frame(seed=13).assign(var_forecast=Z95 * SIGMA)
    frame.iloc[:100, frame.columns.get_loc("var_forecast")] = np.nan
    result = run_tool("kupiec_test", frame, var_column="var_forecast")
    assert result.scalars["nobs"] == 900.0


@pytest.mark.parametrize("name", ("kupiec_test", "christoffersen_test"))
def test_exactly_one_var_input_is_required(name: str):
    frame = normal_frame().assign(var_forecast=Z95 * SIGMA)
    with pytest.raises(ValueError, match="var_column"):
        run_tool(name, frame)  # neither
    with pytest.raises(ValueError, match="var_column"):
        run_tool(name, frame, var_column="var_forecast", var_level=0.02)  # both


def test_too_few_observations_raises_an_actionable_error():
    with pytest.raises(ValueError, match="observations"):
        run_tool("kupiec_test", normal_frame(n=100), var_level=Z95 * SIGMA)


def test_missing_returns_column_raises_an_error_naming_it():
    frame = normal_frame().rename(columns={"return": "r"})
    with pytest.raises(ValueError, match="return"):
        run_tool("kupiec_test", frame, var_level=Z95 * SIGMA)


def test_manifest_records_the_tool():
    result = run_tool("kupiec_test", normal_frame(), var_level=Z95 * SIGMA)
    assert result.manifest.tool == "kupiec_test"
    assert "scipy" in result.manifest.library_versions
