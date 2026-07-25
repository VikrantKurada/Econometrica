import pytest

from tests.econ.fixtures import (
    make_capm_data,
    make_cointegrated_pair,
    make_factor_data,
    make_garch_series,
    make_random_walk,
    make_stationary_ar1,
)


def test_capm_fixture_is_reproducible_under_a_seed():
    a = make_capm_data(beta=1.3, alpha=0.0002, n=500, seed=42)
    b = make_capm_data(beta=1.3, alpha=0.0002, n=500, seed=42)
    assert a.equals(b)


def test_capm_fixture_recovers_beta_under_ols():
    """If the fixture is wrong, every asset-pricing test built on it is wrong."""
    import statsmodels.api as sm

    data = make_capm_data(beta=1.3, alpha=0.0002, n=5000, seed=7, resid_vol=0.005)
    model = sm.OLS(data["asset"], sm.add_constant(data["market"])).fit()
    assert model.params.iloc[1] == pytest.approx(1.3, abs=0.05)


def test_factor_fixture_is_reproducible_under_a_seed():
    loadings = {"mkt_rf": 1.1, "smb": 0.4, "hml": -0.3}
    a = make_factor_data(loadings=loadings, alpha=0.0002, n=500, seed=13)
    b = make_factor_data(loadings=loadings, alpha=0.0002, n=500, seed=13)
    assert a.equals(b)


def test_factor_fixture_recovers_loadings_under_ols():
    """If this fixture is wrong, every factor-model test built on it is wrong."""
    import statsmodels.api as sm

    loadings = {"mkt_rf": 1.1, "smb": 0.4, "hml": -0.3}
    data = make_factor_data(loadings=loadings, alpha=0.0003, n=5000, seed=13, resid_vol=0.005)
    assert list(data.columns) == ["mkt_rf", "smb", "hml", "asset"]
    fit = sm.OLS(data["asset"], sm.add_constant(data[list(loadings)])).fit()
    assert fit.params["const"] == pytest.approx(0.0003, abs=0.0005)
    for name, true_loading in loadings.items():
        assert fit.params[name] == pytest.approx(true_loading, abs=0.05)


def test_random_walk_has_a_unit_root():
    from statsmodels.tsa.stattools import adfuller

    walk = make_random_walk(n=2000, seed=1)
    assert adfuller(walk)[1] > 0.10


def test_stationary_ar1_rejects_the_unit_root():
    from statsmodels.tsa.stattools import adfuller

    ar1 = make_stationary_ar1(phi=0.5, n=2000, seed=1)
    assert adfuller(ar1)[1] < 0.01


def test_garch_fixture_exhibits_volatility_clustering():
    from statsmodels.stats.diagnostic import het_arch

    series = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=3000, seed=3)
    _, p_value, _, _ = het_arch(series, nlags=10)
    assert p_value < 0.01


def test_cointegrated_pair_has_a_stationary_spread():
    from statsmodels.tsa.stattools import adfuller

    x, y = make_cointegrated_pair(n=2000, seed=5)
    spread = y - 1.5 * x
    assert adfuller(spread)[1] < 0.01
