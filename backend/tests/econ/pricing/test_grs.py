"""Known-answer tests for the Gibbons-Ross-Shanken joint alpha test."""

import pandas as pd
import pytest

import econometrica.econ.pricing  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_portfolio_data

N_PORTFOLIOS = 10
T = 2000
PORTFOLIOS = [f"port_{j + 1:02d}" for j in range(N_PORTFOLIOS)]
INJECTED = [0.0003] * 5 + [0.0] * 5  # 3bp/day of mispricing on half the portfolios


def run_grs(data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get("grs_test")
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def null_data() -> pd.DataFrame:
    return make_portfolio_data(n_portfolios=N_PORTFOLIOS, n=T, seed=47)


@pytest.fixture(scope="module")
def mispriced_data() -> pd.DataFrame:
    return make_portfolio_data(n_portfolios=N_PORTFOLIOS, n=T, seed=47, alphas=INJECTED)


def test_grs_is_registered_in_the_pricing_family():
    assert get_registry().get("grs_test").family == "pricing"


def test_grs_fails_to_reject_when_all_true_alphas_are_zero(null_data: pd.DataFrame):
    result = run_grs(null_data, portfolios=PORTFOLIOS, factors=["factor_1"])
    assert result.scalars["grs_p_value"] > 0.10  # comfortably above 0.05
    grs = next(d for d in result.diagnostics if d.name == "grs_f")
    assert grs.passed is True


def test_grs_rejects_injected_alphas_at_the_one_percent_level(mispriced_data: pd.DataFrame):
    result = run_grs(mispriced_data, portfolios=PORTFOLIOS, factors=["factor_1"])
    assert result.scalars["grs_p_value"] < 0.01
    grs = next(d for d in result.diagnostics if d.name == "grs_f")
    assert grs.passed is False
    assert grs.p_value is not None and grs.p_value < 0.01


def test_grs_statistic_distribution_is_explicit(null_data: pd.DataFrame):
    """The finite-sample distribution F(N, T-N-K) must be readable off the result."""
    result = run_grs(null_data, portfolios=PORTFOLIOS, factors=["factor_1"])
    assert result.scalars["df1"] == N_PORTFOLIOS
    assert result.scalars["df2"] == T - N_PORTFOLIOS - 1
    grs = next(d for d in result.diagnostics if d.name == "grs_f")
    assert set(grs.critical_values) == {"5%", "1%"}
    assert grs.critical_values["1%"] > grs.critical_values["5%"] > 1.0
    assert "F(" in grs.interpretation


def test_grs_reports_per_portfolio_alphas(mispriced_data: pd.DataFrame):
    result = run_grs(mispriced_data, portfolios=PORTFOLIOS, factors=["factor_1"])
    assert len(result.estimates) == N_PORTFOLIOS
    for name, true_alpha in zip(PORTFOLIOS, INJECTED, strict=True):
        est = result.estimate(f"alpha_{name}")
        assert est is not None
        assert est.value == pytest.approx(true_alpha, abs=0.0005)
        assert est.std_error is not None and est.std_error > 0


def test_grs_agrees_with_linearmodels_j_statistic(
    null_data: pd.DataFrame, mispriced_data: pd.DataFrame
):
    """Cross-check against an independent implementation (asymptotic J-test)."""
    from linearmodels.asset_pricing import TradedFactorModel

    for data, rejects in ((null_data, False), (mispriced_data, True)):
        j_stat = TradedFactorModel(data[PORTFOLIOS], data[["factor_1"]]).fit().j_statistic
        result = run_grs(data, portfolios=PORTFOLIOS, factors=["factor_1"])
        if rejects:
            assert result.scalars["grs_p_value"] < 0.01 and j_stat.pval < 0.01
        else:
            assert result.scalars["grs_p_value"] > 0.05 and j_stat.pval > 0.05


def test_grs_with_too_few_observations_raises():
    data = make_portfolio_data(n_portfolios=N_PORTFOLIOS, n=12, seed=47)
    with pytest.raises(ValueError, match="observations"):
        run_grs(data, portfolios=PORTFOLIOS, factors=["factor_1"])


def test_grs_with_missing_portfolio_column_raises_naming_it(null_data: pd.DataFrame):
    with pytest.raises(ValueError, match="port_99"):
        run_grs(null_data, portfolios=[*PORTFOLIOS, "port_99"], factors=["factor_1"])


def test_grs_manifest_is_populated(null_data: pd.DataFrame):
    result = run_grs(null_data, portfolios=PORTFOLIOS, factors=["factor_1"])
    assert result.manifest.tool == "grs_test"
    assert {"numpy", "scipy", "statsmodels"} <= set(result.manifest.library_versions)
