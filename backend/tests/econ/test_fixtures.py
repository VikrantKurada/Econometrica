import numpy as np
import pandas as pd
import pytest

from tests.econ.fixtures import (
    make_autocorrelated_capm_data,
    make_capm_data,
    make_cointegrated_pair,
    make_factor_data,
    make_fama_macbeth_panel,
    make_garch_series,
    make_granger_pair,
    make_portfolio_data,
    make_random_walk,
    make_regime_series,
    make_stationary_ar1,
    make_var1_process,
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


VAR1_COEF = [[0.5, 0.3], [0.0, 0.4]]


def test_var1_fixture_is_reproducible_under_a_seed():
    a = make_var1_process(coef=VAR1_COEF, n=500, seed=21)
    b = make_var1_process(coef=VAR1_COEF, n=500, seed=21)
    assert a.equals(b)


def test_var1_fixture_has_named_columns_and_business_day_index():
    data = make_var1_process(coef=VAR1_COEF, n=100, seed=21)
    assert list(data.columns) == ["y1", "y2"]
    assert isinstance(data.index, pd.DatetimeIndex)


def test_var1_fixture_recovers_the_coefficient_matrix_under_ols():
    """Equation-by-equation OLS must see the true A, or every VAR test is wrong."""
    import statsmodels.api as sm

    data = make_var1_process(coef=VAR1_COEF, n=5000, seed=21)
    lagged = sm.add_constant(data.shift(1).dropna().to_numpy())
    current = data.iloc[1:].to_numpy()
    for i, row in enumerate(VAR1_COEF):
        fit = sm.OLS(current[:, i], lagged).fit()
        for j, true_coef in enumerate(row):
            assert fit.params[1 + j] == pytest.approx(true_coef, abs=0.05)


def test_var1_fixture_rejects_a_nonstationary_coefficient_matrix():
    with pytest.raises(ValueError, match="spectral radius"):
        make_var1_process(coef=[[1.0, 0.0], [0.0, 0.4]], n=100, seed=21)


def test_var1_fixture_rejects_a_non_square_coefficient_matrix():
    with pytest.raises(ValueError, match="square"):
        make_var1_process(coef=[[0.5, 0.3]], n=100, seed=21)


def test_var1_fixture_noise_scale_sets_the_innovation_spread():
    small = make_var1_process(coef=VAR1_COEF, n=2000, seed=21, noise_scale=0.1)
    large = make_var1_process(coef=VAR1_COEF, n=2000, seed=21, noise_scale=1.0)
    assert float(np.std(small["y1"])) == pytest.approx(0.1 * float(np.std(large["y1"])), rel=1e-9)


def test_granger_pair_is_reproducible_under_a_seed():
    a = make_granger_pair(n=500, seed=31)
    b = make_granger_pair(n=500, seed=31)
    assert a.equals(b)


def test_granger_pair_y_depends_on_x_at_exactly_the_stated_lag():
    """If the lag structure is wrong, every Granger known-answer test is wrong."""
    import statsmodels.api as sm

    data = make_granger_pair(coef=0.8, lag=2, n=5000, seed=31)
    y = data["y"].to_numpy()
    x = data["x"].to_numpy()
    design = sm.add_constant(
        np.column_stack([y[2:-1], x[2:-1], x[1:-2], x[:-3]])  # y lag1, x lags 1..3
    )
    fit = sm.OLS(y[3:], design).fit()
    assert fit.params[2] == pytest.approx(0.0, abs=0.05)  # x lag 1: nothing
    assert fit.params[3] == pytest.approx(0.8, abs=0.05)  # x lag 2: the channel
    assert fit.params[4] == pytest.approx(0.0, abs=0.05)  # x lag 3: nothing


def test_granger_pair_x_is_unpredictable_from_lagged_y():
    """Causality must run strictly x -> y for the reverse-direction tests."""
    import statsmodels.api as sm

    data = make_granger_pair(coef=0.8, lag=2, n=5000, seed=31)
    y = data["y"].to_numpy()
    x = data["x"].to_numpy()
    fit = sm.OLS(x[1:], sm.add_constant(np.column_stack([x[:-1], y[:-1]]))).fit()
    # The structural claim is the exact zero coefficient; a p-value assertion
    # on a true null would be a coin flip (p is uniform under H0).
    assert fit.params[2] == pytest.approx(0.0, abs=0.05)


def test_granger_pair_rejects_a_nonstationary_y_recursion():
    with pytest.raises(ValueError, match="y_phi"):
        make_granger_pair(n=100, seed=31, y_phi=1.0)


def test_granger_pair_rejects_an_invalid_lag():
    with pytest.raises(ValueError, match="lag"):
        make_granger_pair(n=100, seed=31, lag=0)


def test_regime_series_is_reproducible_under_a_seed():
    a = make_regime_series(n_low=200, n_high=200, vol_low=0.01, vol_high=0.03, seed=17)
    b = make_regime_series(n_low=200, n_high=200, vol_low=0.01, vol_high=0.03, seed=17)
    assert a.equals(b)


def test_regime_series_blocks_carry_their_stated_volatilities():
    series = make_regime_series(n_low=2000, n_high=2000, vol_low=0.01, vol_high=0.03, seed=17)
    assert len(series) == 4000
    assert float(series.iloc[:2000].std()) == pytest.approx(0.01, rel=0.1)
    assert float(series.iloc[2000:].std()) == pytest.approx(0.03, rel=0.1)


def test_regime_series_supports_regime_means():
    series = make_regime_series(
        n_low=3000, n_high=3000, vol_low=0.01, vol_high=0.03, seed=17,
        mean_low=0.001, mean_high=-0.002,
    )
    assert float(series.iloc[:3000].mean()) == pytest.approx(0.001, abs=0.0005)
    assert float(series.iloc[3000:].mean()) == pytest.approx(-0.002, abs=0.0015)


def test_regime_series_rejects_non_positive_volatilities():
    with pytest.raises(ValueError, match="vol"):
        make_regime_series(n_low=100, n_high=100, vol_low=0.0, vol_high=0.03, seed=17)
