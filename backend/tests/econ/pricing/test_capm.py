"""Known-answer tests for the CAPM tool.

Every numeric assertion here is against a truth the fixture guarantees, not
against a previously captured output.
"""

import pandas as pd
import pytest

import econometrica.econ.pricing  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_capm_data


def run_capm(data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get("capm")
    return tool.fn(data, tool.params_model(**params))


def test_capm_is_registered_in_the_pricing_family():
    tool = get_registry().get("capm")
    assert tool.family == "pricing"
    assert tool.version


def test_capm_recovers_the_true_beta_and_alpha():
    data = make_capm_data(beta=1.3, alpha=0.0002, n=5000, seed=7, resid_vol=0.005)
    result = run_capm(data)

    beta = result.estimate("beta")
    alpha = result.estimate("alpha")
    assert beta is not None and alpha is not None
    assert beta.value == pytest.approx(1.3, abs=0.05)
    assert alpha.value == pytest.approx(0.0002, abs=0.0005)
    # true R^2 = beta^2 var(mkt) / (beta^2 var(mkt) + resid_vol^2) ~= 0.87
    assert 0.80 < result.scalars["r_squared"] < 0.95
    assert result.scalars["nobs"] == 5000


def test_capm_estimates_carry_full_inference():
    data = make_capm_data(beta=1.3, alpha=0.0002, n=5000, seed=7, resid_vol=0.005)
    result = run_capm(data)
    beta = result.estimate("beta")
    assert beta is not None
    assert beta.std_error is not None and beta.std_error > 0
    assert beta.t_stat is not None and beta.t_stat > 10
    assert beta.p_value is not None and beta.p_value < 0.001
    assert beta.ci_low is not None and beta.ci_high is not None
    assert beta.ci_low < 1.3 < beta.ci_high


def test_regressing_a_series_on_itself_is_the_identity():
    """The canonical invariant: beta = 1, alpha = 0, R^2 = 1."""
    market = make_capm_data(beta=1.0, alpha=0.0, n=1000, seed=3)["market"]
    data = pd.DataFrame({"market": market, "asset": market})
    result = run_capm(data)

    beta = result.estimate("beta")
    alpha = result.estimate("alpha")
    assert beta is not None and beta.value == pytest.approx(1.0, abs=1e-10)
    assert alpha is not None and alpha.value == pytest.approx(0.0, abs=1e-12)
    assert result.scalars["r_squared"] == pytest.approx(1.0, abs=1e-10)


def test_capm_annualises_alpha():
    data = make_capm_data(beta=1.3, alpha=0.0002, n=5000, seed=7, resid_vol=0.005)
    result = run_capm(data)
    alpha = result.estimate("alpha")
    assert alpha is not None
    expected = (1.0 + alpha.value) ** 252 - 1.0
    assert result.scalars["alpha_annualised"] == pytest.approx(expected)


def test_capm_with_a_zero_risk_free_column_matches_raw_regression():
    data = make_capm_data(beta=1.1, alpha=0.0001, n=2000, seed=11)
    with_rf = data.assign(rf=0.0)
    raw = run_capm(data)
    excess = run_capm(with_rf, risk_free="rf")
    raw_beta = raw.estimate("beta")
    excess_beta = excess.estimate("beta")
    assert raw_beta is not None and excess_beta is not None
    assert excess_beta.value == pytest.approx(raw_beta.value)


def test_capm_emits_residuals_series_with_iso_dates():
    data = make_capm_data(beta=1.3, alpha=0.0002, n=500, seed=7)
    result = run_capm(data)
    residuals = result.series["residuals"]
    assert len(residuals.y) == 500
    assert residuals.x[0] == "2015-01-02"
    assert all(isinstance(x, str) for x in residuals.x)


def test_capm_runs_residual_diagnostics():
    data = make_capm_data(beta=1.3, alpha=0.0002, n=2000, seed=7)
    result = run_capm(data)
    names = {d.name for d in result.diagnostics}
    assert {"jarque_bera", "durbin_watson"} <= names
    # Gaussian iid residuals by construction: both checks should pass.
    for diag in result.diagnostics:
        assert diag.passed is True, f"{diag.name} unexpectedly failed on clean data"


def test_manifest_fingerprint_is_stable_and_params_sensitive():
    data = make_capm_data(beta=1.3, alpha=0.0002, n=500, seed=7)
    a = run_capm(data)
    b = run_capm(data)
    changed = run_capm(data, min_obs=50)

    assert a.manifest.data_fingerprint == b.manifest.data_fingerprint
    assert a.manifest.params_hash == b.manifest.params_hash
    assert changed.manifest.params_hash != a.manifest.params_hash
    assert a.manifest.tool == "capm"
    assert "statsmodels" in a.manifest.library_versions


def test_too_few_observations_raises_an_actionable_error():
    data = make_capm_data(beta=1.0, alpha=0.0, n=10, seed=1)
    with pytest.raises(ValueError, match="observations"):
        run_capm(data)


def test_missing_column_raises_an_error_naming_it():
    data = make_capm_data(beta=1.0, alpha=0.0, n=100, seed=1).drop(columns=["market"])
    with pytest.raises(ValueError, match="market"):
        run_capm(data)
