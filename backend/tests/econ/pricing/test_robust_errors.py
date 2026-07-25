"""Known-answer tests for the shared covariance options (nonrobust/white/newey_west).

The HAC case is the plan's canonical check: on AR(1) errors with a persistent
regressor, Newey-West standard errors must exceed nonrobust ones while the
point estimates stay bit-for-bit identical.
"""

import pandas as pd
import pytest

import econometrica.econ.pricing  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_autocorrelated_capm_data, make_factor_data


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def ar_data() -> pd.DataFrame:
    return make_autocorrelated_capm_data(
        beta=1.2, alpha=0.0003, phi=0.7, n=3000, seed=23, market_phi=0.5
    )


def test_hac_errors_exceed_nonrobust_on_autocorrelated_residuals(ar_data: pd.DataFrame):
    plain = run_tool("capm", ar_data, cov="nonrobust")
    hac = run_tool("capm", ar_data, cov="newey_west")

    for name in ("alpha", "beta"):
        plain_est = plain.estimate(name)
        hac_est = hac.estimate(name)
        assert plain_est is not None and hac_est is not None
        assert plain_est.std_error is not None and hac_est.std_error is not None
        assert hac_est.std_error > plain_est.std_error, (
            f"{name}: HAC se {hac_est.std_error} should exceed nonrobust {plain_est.std_error}"
        )


def test_point_estimates_are_identical_across_cov_choices(ar_data: pd.DataFrame):
    results = {
        cov: run_tool("capm", ar_data, cov=cov) for cov in ("nonrobust", "white", "newey_west")
    }
    for name in ("alpha", "beta"):
        values = set()
        for result in results.values():
            est = result.estimate(name)
            assert est is not None
            values.add(est.value)
        assert len(values) == 1, f"{name}: point estimates differ across cov types: {values}"


def test_white_errors_differ_from_nonrobust_but_estimates_do_not(ar_data: pd.DataFrame):
    plain = run_tool("capm", ar_data, cov="nonrobust")
    white = run_tool("capm", ar_data, cov="white")
    plain_beta = plain.estimate("beta")
    white_beta = white.estimate("beta")
    assert plain_beta is not None and white_beta is not None
    assert white_beta.value == plain_beta.value
    assert white_beta.std_error != plain_beta.std_error


def test_newey_west_default_lag_rule():
    from econometrica.econ.pricing.robust_errors import newey_west_default_lags

    # floor(4 * (n/100)^(2/9))
    assert newey_west_default_lags(100) == 4
    assert newey_west_default_lags(2000) == 7
    assert newey_west_default_lags(3000) == 8


def test_hac_lags_default_is_reported_and_overridable(ar_data: pd.DataFrame):
    auto = run_tool("capm", ar_data, cov="newey_west")
    manual = run_tool("capm", ar_data, cov="newey_west", hac_lags=12)

    assert auto.scalars["hac_lags"] == 8  # floor(4 * 30^(2/9)) at n=3000
    assert manual.scalars["hac_lags"] == 12
    auto_beta = auto.estimate("beta")
    manual_beta = manual.estimate("beta")
    assert auto_beta is not None and manual_beta is not None
    assert manual_beta.std_error != auto_beta.std_error
    assert "hac_lags" not in run_tool("capm", ar_data).scalars


def test_cov_choice_changes_the_params_hash(ar_data: pd.DataFrame):
    plain = run_tool("capm", ar_data)
    hac = run_tool("capm", ar_data, cov="newey_west")
    assert plain.manifest.params_hash != hac.manifest.params_hash


def test_factor_models_share_the_cov_options():
    loadings = {"mkt_rf": 1.1, "smb": 0.4, "hml": -0.3}
    data = make_factor_data(loadings=loadings, alpha=0.0002, n=2000, seed=13)
    plain = run_tool("ff3", data, cov="nonrobust")
    hac = run_tool("ff3", data, cov="newey_west")

    assert hac.scalars["hac_lags"] == 7
    for factor in loadings:
        plain_est = plain.estimate(factor)
        hac_est = hac.estimate(factor)
        assert plain_est is not None and hac_est is not None
        assert hac_est.value == plain_est.value
        assert hac_est.std_error != plain_est.std_error
