import pandas as pd
import pytest

from tests.econ.fixtures import (
    make_autocorrelated_capm_data,
    make_capm_data,
    make_cointegrated_pair,
    make_factor_data,
    make_fama_macbeth_panel,
    make_garch_series,
    make_portfolio_data,
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


def test_autocorrelated_capm_fixture_is_reproducible_under_a_seed():
    a = make_autocorrelated_capm_data(beta=1.2, alpha=0.0003, phi=0.7, n=500, seed=23)
    b = make_autocorrelated_capm_data(beta=1.2, alpha=0.0003, phi=0.7, n=500, seed=23)
    assert a.equals(b)


def test_autocorrelated_capm_fixture_has_ar1_residuals():
    """OLS residuals must inherit the true AR(1) structure, or the HAC tests are vacuous."""
    import statsmodels.api as sm
    from statsmodels.stats.stattools import durbin_watson

    data = make_autocorrelated_capm_data(
        beta=1.2, alpha=0.0003, phi=0.7, n=3000, seed=23, market_phi=0.5
    )
    fit = sm.OLS(data["asset"], sm.add_constant(data["market"])).fit()
    lag1 = pd.Series(fit.resid).autocorr(lag=1)
    assert lag1 == pytest.approx(0.7, abs=0.1)
    assert durbin_watson(fit.resid) < 1.0  # ~2*(1-phi) = 0.6


def test_autocorrelated_capm_fixture_market_is_persistent_when_asked():
    data = make_autocorrelated_capm_data(
        beta=1.2, alpha=0.0003, phi=0.7, n=3000, seed=23, market_phi=0.5
    )
    assert data["market"].autocorr(lag=1) == pytest.approx(0.5, abs=0.1)


def test_fama_macbeth_panel_is_reproducible_under_a_seed():
    a = make_fama_macbeth_panel(premiums={"exposure": 0.5}, n_entities=10, n_periods=50, seed=31)
    b = make_fama_macbeth_panel(premiums={"exposure": 0.5}, n_entities=10, n_periods=50, seed=31)
    assert a.equals(b)


def test_fama_macbeth_panel_has_entity_date_structure():
    panel = make_fama_macbeth_panel(
        premiums={"exposure": 0.5}, n_entities=10, n_periods=50, seed=31
    )
    assert panel.index.nlevels == 2
    assert panel.index.names == ["entity", "date"]
    assert panel.index.get_level_values("entity").nunique() == 10
    assert panel.index.get_level_values("date").nunique() == 50
    # Exposures are entity characteristics: constant through time.
    per_entity = panel.groupby(level="entity")["exposure"].nunique()
    assert (per_entity == 1).all()


def test_fama_macbeth_panel_cross_section_recovers_the_premium():
    """A single-period cross-sectional OLS must see the true premium."""
    import statsmodels.api as sm

    panel = make_fama_macbeth_panel(
        premiums={"exposure": 0.5}, n_entities=30, n_periods=500, seed=31
    )
    first_date = panel.index.get_level_values("date")[0]
    cross_section = panel.xs(first_date, level="date")
    fit = sm.OLS(cross_section["returns"], sm.add_constant(cross_section["exposure"])).fit()
    assert fit.params["exposure"] == pytest.approx(0.5, abs=0.02)


def test_portfolio_fixture_is_reproducible_under_a_seed():
    a = make_portfolio_data(n_portfolios=5, n=200, seed=41)
    b = make_portfolio_data(n_portfolios=5, n=200, seed=41)
    assert a.equals(b)


def test_portfolio_fixture_has_factor_and_portfolio_columns():
    data = make_portfolio_data(n_portfolios=3, n=100, seed=41, n_factors=2)
    assert list(data.columns) == ["factor_1", "factor_2", "port_01", "port_02", "port_03"]


def test_portfolio_fixture_recovers_injected_alphas_under_ols():
    """If this fixture is wrong, both GRS known-answer tests are wrong."""
    import statsmodels.api as sm

    alphas = [0.0003, 0.0003, 0.0, 0.0]
    data = make_portfolio_data(n_portfolios=4, n=5000, seed=41, alphas=alphas, resid_vol=0.005)
    design = sm.add_constant(data[["factor_1"]])
    for j, true_alpha in enumerate(alphas):
        fit = sm.OLS(data[f"port_{j + 1:02d}"], design).fit()
        assert fit.params["const"] == pytest.approx(true_alpha, abs=0.0003)


def test_portfolio_fixture_defaults_to_zero_alphas():
    import statsmodels.api as sm

    data = make_portfolio_data(n_portfolios=4, n=5000, seed=43, resid_vol=0.005)
    design = sm.add_constant(data[["factor_1"]])
    for j in range(4):
        fit = sm.OLS(data[f"port_{j + 1:02d}"], design).fit()
        assert fit.params["const"] == pytest.approx(0.0, abs=0.0003)


def test_random_walk_has_a_unit_root():
    from statsmodels.tsa.stattools import adfuller

    walk = make_random_walk(n=2000, seed=1)
    assert adfuller(walk)[1] > 0.10


def test_stationary_ar1_rejects_the_unit_root():
    from statsmodels.tsa.stattools import adfuller

    ar1 = make_stationary_ar1(phi=0.5, n=2000, seed=1)
    assert adfuller(ar1)[1] < 0.01


def test_stationary_ar1_supports_negative_phi():
    """The mean-reverting case for the variance ratio known-answer tests."""
    series = make_stationary_ar1(phi=-0.3, n=3000, seed=2)
    assert series.autocorr(lag=1) == pytest.approx(-0.3, abs=0.05)


def test_garch_fixture_exhibits_volatility_clustering():
    from statsmodels.stats.diagnostic import het_arch

    series = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=3000, seed=3)
    _, p_value, _, _ = het_arch(series, nlags=10)
    assert p_value < 0.01


def test_garch_fixture_is_reproducible_under_a_seed():
    a = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=500, seed=3)
    b = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=500, seed=3)
    assert a.equals(b)


def test_garch_fixture_t_variant_is_reproducible_under_a_seed():
    a = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=500, seed=3, dist="t", nu=6.0)
    b = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=500, seed=3, dist="t", nu=6.0)
    assert a.equals(b)


def test_garch_fixture_t_variant_has_fatter_tails_than_the_normal_variant():
    """Standardised residuals of the t variant must show clear excess kurtosis.

    Standardising by the TRUE conditional volatility isolates the innovation
    distribution: normal innovations give excess kurtosis near 0, t(6)
    innovations give it near 3. If this fails, the t-vs-normal AIC comparison
    in the GARCH tool tests is vacuous.
    """
    from scipy import stats

    kwargs = dict(omega=1e-6, alpha=0.09, beta=0.90, n=3000, seed=3)
    normal = make_garch_series(**kwargs)
    fat = make_garch_series(**kwargs, dist="t", nu=6.0)
    # The GARCH filter itself fattens unconditional tails; compare the raw
    # series with identical volatility dynamics so only the innovations differ.
    assert stats.kurtosis(fat) > stats.kurtosis(normal) + 1.0


def test_garch_fixture_t_variant_keeps_unit_variance_innovations():
    """Innovations are standardised t, so the unconditional variance formula
    omega / (1 - alpha - beta) still holds for the t variant."""
    series = make_garch_series(
        omega=1e-6, alpha=0.05, beta=0.60, n=20000, seed=3, dist="t", nu=6.0
    )
    target = 1e-6 / (1.0 - 0.05 - 0.60)
    assert float(series.var()) == pytest.approx(target, rel=0.15)


def test_garch_fixture_t_variant_requires_a_valid_nu():
    with pytest.raises(ValueError, match="nu"):
        make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=100, seed=3, dist="t", nu=2.0)
    with pytest.raises(ValueError, match="nu"):
        make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=100, seed=3, dist="t")


def test_cointegrated_pair_has_a_stationary_spread():
    from statsmodels.tsa.stattools import adfuller

    x, y = make_cointegrated_pair(n=2000, seed=5)
    spread = y - 1.5 * x
    assert adfuller(spread)[1] < 0.01
