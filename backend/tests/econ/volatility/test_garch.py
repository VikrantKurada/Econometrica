"""Known-answer tests for the GARCH family (garch, egarch, gjr_garch).

The data comes from ``make_garch_series``, which iterates the GARCH(1,1)
recursion directly — independently of the ``arch`` package under test — so
parameter recovery is a genuine known answer, not a round trip.

Scale convention under test: parameter estimates are reported on the
percentage-return scale (returns x 100, arch's recommended scaling), so the
true omega of 1e-6 in decimal units is 0.01 in percent^2. Everything in
return units — the conditional volatility series and the unconditional
volatility scalars — must come back in DECIMAL units.
"""

import math

import numpy as np
import pandas as pd
import pytest

import econometrica.econ.volatility  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_garch_series

TRUE = {"omega": 1e-6, "alpha": 0.09, "beta": 0.90}


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


def garch_frame(seed: int = 3, n: int = 3000, **kwargs: object) -> pd.DataFrame:
    series = make_garch_series(n=n, seed=seed, **TRUE, **kwargs)  # type: ignore[arg-type]
    return pd.DataFrame({"return": series})


@pytest.fixture(scope="module")
def garch_result() -> ResultSet:
    return run_tool("garch", garch_frame())


@pytest.fixture(scope="module")
def egarch_result() -> ResultSet:
    return run_tool("egarch", garch_frame())


@pytest.fixture(scope="module")
def gjr_result() -> ResultSet:
    return run_tool("gjr_garch", garch_frame())


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


def test_garch_tools_are_registered_in_the_volatility_family():
    for name in ("garch", "egarch", "gjr_garch"):
        tool = get_registry().get(name)
        assert tool.family == "volatility"
        assert tool.version


@pytest.mark.parametrize("seed", [3, 7])
def test_garch_confidence_intervals_recover_the_true_parameters(seed: int):
    """The core known answer: data generated OUTSIDE arch, parameters inside the CIs.

    Two committed seeds guard against a single lucky draw; the fitted scale is
    percentage returns, so true omega 1e-6 becomes 0.01.
    """
    result = run_tool("garch", garch_frame(seed=seed))
    alpha = result.estimate("alpha[1]")
    beta = result.estimate("beta[1]")
    omega = result.estimate("omega")
    assert alpha is not None and beta is not None and omega is not None
    assert alpha.ci_low is not None and alpha.ci_low <= 0.09 <= (alpha.ci_high or -1.0)
    assert beta.ci_low is not None and beta.ci_low <= 0.90 <= (beta.ci_high or -1.0)
    assert omega.ci_low is not None and omega.ci_low <= 0.01 <= (omega.ci_high or -1.0)
    for est in (alpha, beta, omega):
        assert est.std_error is not None and est.std_error > 0
        assert est.t_stat is not None and est.p_value is not None


def test_garch_persistence_equals_alpha_plus_beta(garch_result: ResultSet):
    alpha = garch_result.estimate("alpha[1]")
    beta = garch_result.estimate("beta[1]")
    assert alpha is not None and beta is not None
    assert garch_result.scalars["persistence"] == pytest.approx(alpha.value + beta.value)


def test_garch_half_life_is_consistent_with_persistence(garch_result: ResultSet):
    persistence = garch_result.scalars["persistence"]
    assert 0.0 < persistence < 1.0
    expected = math.log(0.5) / math.log(persistence)
    assert garch_result.scalars["half_life"] == pytest.approx(expected)


def test_garch_conditional_volatility_is_positive_full_length_and_decimal(
    garch_result: ResultSet,
):
    frame = garch_frame()
    vol = garch_result.series["conditional_volatility"]
    values = np.array([v for v in vol.y if v is not None])
    assert len(vol.y) == len(frame) == len(values)
    assert (values > 0).all()
    # Decimal units: the average conditional vol must sit near the sample
    # std of the DECIMAL returns (0.01-ish), not 100x it.
    sample_std = float(frame["return"].std())
    assert 0.5 * sample_std < values.mean() < 2.0 * sample_std


def test_garch_unconditional_vol_is_decimal_and_annualised(garch_result: ResultSet):
    """True unconditional vol is sqrt(1e-6 / (1 - 0.99)) = 0.01 per period."""
    per_period = garch_result.scalars["unconditional_vol"]
    annualised = garch_result.scalars["unconditional_vol_annualized"]
    assert per_period == pytest.approx(0.01, rel=0.5)
    assert annualised == pytest.approx(per_period * math.sqrt(252.0))


def test_garch_standardized_residuals_have_roughly_unit_variance(garch_result: ResultSet):
    resid = garch_result.series["standardized_residuals"]
    values = np.array([v for v in resid.y if v is not None])
    assert len(values) == len(garch_frame())
    assert 0.8 < values.std() < 1.2


def test_garch_residual_diagnostics_pass_on_well_specified_data(garch_result: ResultSet):
    """A correct GARCH(1,1) fit leaves no autocorrelation and no remaining ARCH."""
    lb = diagnostic(garch_result, "ljung_box_std_resid")
    lb_sq = diagnostic(garch_result, "ljung_box_std_resid_sq")
    for diag in (lb, lb_sq):
        assert diag.p_value is not None and diag.p_value >= 0.05
        assert diag.passed is True


def test_garch_convergence_diagnostic_reports_success(garch_result: ResultSet):
    conv = diagnostic(garch_result, "convergence")
    assert conv.statistic == 0.0
    assert conv.passed is True


def test_garch_reports_fit_statistics(garch_result: ResultSet):
    scalars = garch_result.scalars
    assert scalars["nobs"] == 3000.0
    assert scalars["aic"] == pytest.approx(-2.0 * scalars["log_likelihood"] + 2.0 * 4)
    assert scalars["bic"] > scalars["aic"]  # log(3000) > 2


def test_garch_zero_mean_drops_mu():
    result = run_tool("garch", garch_frame(seed=7, n=1000), mean="zero")
    assert result.estimate("mu") is None
    assert result.estimate("omega") is not None


def test_egarch_converges_and_persistence_is_beta_only(egarch_result: ResultSet):
    """EGARCH persistence lives on the log-variance AR: beta alone, not alpha+beta."""
    conv = diagnostic(egarch_result, "convergence")
    assert conv.passed is True
    beta = egarch_result.estimate("beta[1]")
    assert beta is not None
    assert egarch_result.scalars["persistence"] == pytest.approx(beta.value)


def test_egarch_conditional_volatility_is_positive_and_decimal(egarch_result: ResultSet):
    values = np.array([v for v in egarch_result.series["conditional_volatility"].y])
    sample_std = float(garch_frame()["return"].std())
    assert (values > 0).all()
    assert 0.5 * sample_std < values.mean() < 2.0 * sample_std


def test_gjr_gamma_is_insignificant_on_symmetric_data(gjr_result: ResultSet):
    """The fixture's innovations are symmetric, so the leverage term is truly zero."""
    conv = diagnostic(gjr_result, "convergence")
    assert conv.passed is True
    gamma = gjr_result.estimate("gamma[1]")
    assert gamma is not None
    assert gamma.p_value is not None and gamma.p_value > 0.05


def test_gjr_persistence_uses_the_half_gamma_formula(gjr_result: ResultSet):
    alpha = gjr_result.estimate("alpha[1]")
    gamma = gjr_result.estimate("gamma[1]")
    beta = gjr_result.estimate("beta[1]")
    assert alpha is not None and gamma is not None and beta is not None
    expected = alpha.value + 0.5 * gamma.value + beta.value
    assert gjr_result.scalars["persistence"] == pytest.approx(expected)


def test_t_distribution_recovers_nu_and_beats_normal_on_fat_tailed_data():
    """t innovations with nu = 6: the t fit's nu CI contains 6 and its AIC wins."""
    frame = garch_frame(seed=3, dist="t", nu=6.0)
    t_result = run_tool("garch", frame, dist="t")
    normal_result = run_tool("garch", frame, dist="normal")
    nu = t_result.estimate("nu")
    assert nu is not None
    assert nu.ci_low is not None and nu.ci_high is not None
    assert nu.ci_low <= 6.0 <= nu.ci_high
    assert t_result.scalars["aic"] < normal_result.scalars["aic"]


def test_garch_results_are_bit_identical_across_runs():
    frame = garch_frame(seed=7, n=1000)
    a = run_tool("garch", frame)
    b = run_tool("garch", frame)
    assert a.scalars == b.scalars
    assert [e.value for e in a.estimates] == [e.value for e in b.estimates]


def test_constant_series_raises_an_actionable_error():
    """arch cannot converge on a zero-variance series; the tool must say so."""
    frame = pd.DataFrame(
        {"return": np.zeros(500)}, index=pd.bdate_range("2020-01-01", periods=500)
    )
    with pytest.raises(ValueError, match="garch"):
        run_tool("garch", frame)


def test_too_few_observations_raises_an_actionable_error():
    with pytest.raises(ValueError, match="observations"):
        run_tool("garch", garch_frame(n=50))


def test_missing_column_raises_an_error_naming_it():
    with pytest.raises(ValueError, match="return"):
        run_tool("garch", garch_frame().rename(columns={"return": "ret"}))


def test_manifest_records_the_tool_and_the_arch_version(garch_result: ResultSet):
    assert garch_result.manifest.tool == "garch"
    assert "arch" in garch_result.manifest.library_versions
