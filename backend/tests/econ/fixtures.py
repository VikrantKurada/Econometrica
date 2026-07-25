"""Synthetic data generators with known true parameters.

Known-answer testing needs data whose true parameters are known. Every
econometric tool test (Tasks 2.6-2.19) imports these generators, so the
generators themselves are tested in ``test_fixtures.py``: if a fixture is
wrong, every test built on it is silently wrong.

Every generator takes an explicit ``seed`` and draws from
``numpy.random.default_rng(seed)``, so identical calls are bit-for-bit
reproducible. All output carries a business-day ``DatetimeIndex`` because the
tools consume date-indexed data.
"""

import numpy as np
import pandas as pd

_START = "2015-01-02"


def _bdays(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start=_START, periods=n)


def make_capm_data(
    *,
    beta: float,
    alpha: float,
    n: int,
    seed: int,
    resid_vol: float = 0.01,
    market_mean: float = 0.0004,
    market_vol: float = 0.01,
) -> pd.DataFrame:
    """Daily returns obeying ``asset = alpha + beta * market + eps`` exactly.

    True parameters: intercept ``alpha``, slope ``beta``; ``eps`` is i.i.d.
    N(0, resid_vol^2) and independent of the market, so OLS on (market, asset)
    must recover ``beta`` to within sampling error. The market is i.i.d.
    N(market_mean, market_vol^2) at a realistic daily-return scale.

    Downstream tests rely on: columns named ``market`` and ``asset``, a
    business-day DatetimeIndex, and zero specification error.
    """
    rng = np.random.default_rng(seed)
    market = rng.normal(market_mean, market_vol, n)
    asset = alpha + beta * market + rng.normal(0.0, resid_vol, n)
    return pd.DataFrame({"market": market, "asset": asset}, index=_bdays(n))


def make_autocorrelated_capm_data(
    *,
    beta: float,
    alpha: float,
    phi: float,
    n: int,
    seed: int,
    resid_vol: float = 0.01,
    market_phi: float = 0.0,
    market_mean: float = 0.0004,
    market_vol: float = 0.01,
    burn_in: int = 500,
) -> pd.DataFrame:
    """CAPM data whose errors are a stationary AR(1): the HAC known-answer case.

    ``asset = alpha + beta * market + eps`` with ``eps_t = phi * eps_{t-1} + nu_t``,
    innovations scaled so eps keeps unconditional standard deviation
    ``resid_vol``. The market is optionally AR(1) with coefficient
    ``market_phi`` (same unconditional scale ``market_vol``): serial
    correlation in the errors inflates the HAC standard error of the slope
    only when the regressor is itself persistent (the score ``x_t * eps_t``
    autocorrelates like ``market_phi * phi``), so HAC-vs-nonrobust slope
    comparisons need ``market_phi > 0``. The intercept's error variance is
    inflated by autocorrelated errors alone.

    True parameters: ``alpha``, ``beta``, residual AR coefficient ``phi``.
    Both AR processes discard ``burn_in`` observations.
    """
    if not abs(phi) < 1:
        raise ValueError(f"residual AR(1) is stationary only for |phi| < 1, got {phi}")
    if not abs(market_phi) < 1:
        raise ValueError(f"market AR(1) is stationary only for |market_phi| < 1, got {market_phi}")
    rng = np.random.default_rng(seed)
    total = n + burn_in

    market_innov_sd = market_vol * np.sqrt(1.0 - market_phi**2)
    market_shocks = rng.normal(0.0, market_innov_sd, total)
    market = np.empty(total)
    market[0] = market_shocks[0]
    for t in range(1, total):
        market[t] = market_phi * market[t - 1] + market_shocks[t]
    market = market[burn_in:] + market_mean

    resid_innov_sd = resid_vol * np.sqrt(1.0 - phi**2)
    nu = rng.normal(0.0, resid_innov_sd, total)
    eps = np.empty(total)
    eps[0] = nu[0]
    for t in range(1, total):
        eps[t] = phi * eps[t - 1] + nu[t]
    eps = eps[burn_in:]

    asset = alpha + beta * market + eps
    return pd.DataFrame({"market": market, "asset": asset}, index=_bdays(n))


def make_factor_data(
    *,
    loadings: dict[str, float],
    alpha: float,
    n: int,
    seed: int,
    resid_vol: float = 0.01,
    factor_mean: float = 0.0002,
    factor_vol: float = 0.01,
) -> pd.DataFrame:
    """Daily returns obeying ``asset = alpha + sum(loading_i * factor_i) + eps``.

    True parameters: intercept ``alpha`` and one loading per entry of
    ``loadings`` (insertion order fixes both column order and rng draw order,
    so identical calls are bit-for-bit reproducible). Factors are i.i.d.
    N(factor_mean, factor_vol^2), mutually independent and independent of the
    i.i.d. N(0, resid_vol^2) residual, so multivariate OLS must recover every
    loading to within sampling error — including exact zeros.

    Downstream tests rely on: one column per factor named exactly as in
    ``loadings``, an ``asset`` column, a business-day DatetimeIndex, and zero
    specification error.
    """
    rng = np.random.default_rng(seed)
    factors = {name: rng.normal(factor_mean, factor_vol, n) for name in loadings}
    asset = alpha + rng.normal(0.0, resid_vol, n)
    for name, loading in loadings.items():
        asset = asset + loading * factors[name]
    return pd.DataFrame({**factors, "asset": asset}, index=_bdays(n))


def make_fama_macbeth_panel(
    *,
    premiums: dict[str, float],
    n_entities: int,
    n_periods: int,
    seed: int,
    intercept: float = 0.0,
    noise_vol: float = 0.01,
    exposure_mean: float = 1.0,
    exposure_vol: float = 0.5,
) -> pd.DataFrame:
    """An (entity, date) panel obeying ``r_it = intercept + sum(premium_k * exposure_ik) + eps``.

    True parameters: one constant risk premium per entry of ``premiums`` and
    the ``intercept`` (the zero-beta rate). Exposures are entity
    characteristics — drawn once per entity from N(exposure_mean,
    exposure_vol^2) and constant through time — while ``eps`` is i.i.d.
    N(0, noise_vol^2) across entities and periods, so every period's
    cross-sectional OLS recovers the premiums to within sampling error and
    Fama-MacBeth averaging tightens them further.

    Downstream tests rely on: a two-level MultiIndex named (entity, date)
    with business-day dates, a ``returns`` column, and one column per
    exposure named exactly as in ``premiums``.
    """
    rng = np.random.default_rng(seed)
    exposures = {name: rng.normal(exposure_mean, exposure_vol, n_entities) for name in premiums}
    eps = rng.normal(0.0, noise_vol, (n_entities, n_periods))

    expected = intercept + sum(
        premium * exposures[name] for name, premium in premiums.items()
    )
    returns = np.asarray(expected)[:, None] + eps

    entities = [f"asset_{i:02d}" for i in range(n_entities)]
    dates = _bdays(n_periods)
    index = pd.MultiIndex.from_product([entities, dates], names=["entity", "date"])
    frame = pd.DataFrame({"returns": returns.ravel()}, index=index)
    for name in premiums:
        frame[name] = np.repeat(exposures[name], n_periods)
    return frame


def make_portfolio_data(
    *,
    n_portfolios: int,
    n: int,
    seed: int,
    alphas: list[float] | None = None,
    n_factors: int = 1,
    resid_vol: float = 0.005,
    factor_mean: float = 0.0004,
    factor_vol: float = 0.01,
    beta_low: float = 0.5,
    beta_high: float = 1.5,
) -> pd.DataFrame:
    """Test-portfolio excess returns generated exactly from traded factors.

    ``port_j = alpha_j + sum_k beta_jk * factor_k + eps_j`` with factors i.i.d.
    N(factor_mean, factor_vol^2), betas drawn once per portfolio from
    U(beta_low, beta_high), and residuals i.i.d. N(0, resid_vol^2) that are
    independent across portfolios and of the factors.

    True parameters: the per-portfolio intercepts ``alphas`` (all zero when
    None — the exact null of the GRS test) and the factor betas. Downstream
    tests rely on: columns ``factor_1..factor_K`` then ``port_01..port_NN``,
    a business-day DatetimeIndex, zero specification error, and a diagonal
    true residual covariance.
    """
    if alphas is not None and len(alphas) != n_portfolios:
        raise ValueError(f"expected {n_portfolios} alphas, got {len(alphas)}")
    true_alphas = alphas if alphas is not None else [0.0] * n_portfolios
    rng = np.random.default_rng(seed)

    factors = {f"factor_{k + 1}": rng.normal(factor_mean, factor_vol, n) for k in range(n_factors)}
    betas = rng.uniform(beta_low, beta_high, (n_portfolios, n_factors))
    columns: dict[str, np.ndarray] = dict(factors)
    for j in range(n_portfolios):
        port = true_alphas[j] + rng.normal(0.0, resid_vol, n)
        for k, factor in enumerate(factors.values()):
            port = port + betas[j, k] * factor
        columns[f"port_{j + 1:02d}"] = port
    return pd.DataFrame(columns, index=_bdays(n))


def make_random_walk(n: int, seed: int, step_vol: float = 1.0) -> pd.Series:
    """A driftless random walk: cumulative sum of i.i.d. N(0, step_vol^2) steps.

    True property: exactly one unit root, so ADF must fail to reject.
    Downstream tests rely on this being I(1) with no drift and no trend.
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, step_vol, n)
    return pd.Series(np.cumsum(steps), index=_bdays(n), name="random_walk")


def make_stationary_ar1(
    *, phi: float, n: int, seed: int, sigma: float = 1.0, burn_in: int = 500
) -> pd.Series:
    """A stationary AR(1): ``x_t = phi * x_{t-1} + eps_t`` with |phi| < 1.

    True parameters: autoregressive coefficient ``phi``, innovation scale
    ``sigma``. A ``burn_in`` period is discarded so the series starts from the
    stationary distribution rather than from zero. Downstream tests rely on
    ADF rejecting the unit root and on the true phi being recoverable.
    """
    if not abs(phi) < 1:
        raise ValueError(f"AR(1) is stationary only for |phi| < 1, got {phi}")
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, sigma, n + burn_in)
    x = np.empty(n + burn_in)
    x[0] = eps[0]
    for t in range(1, n + burn_in):
        x[t] = phi * x[t - 1] + eps[t]
    return pd.Series(x[burn_in:], index=_bdays(n), name="ar1")


def make_garch_series(
    *,
    omega: float,
    alpha: float,
    beta: float,
    n: int,
    seed: int,
    burn_in: int = 500,
    dist: str = "normal",
    nu: float | None = None,
) -> pd.Series:
    """Zero-mean returns from a GARCH(1,1) iterated directly, not via ``arch``.

    Recursion: ``sigma2_t = omega + alpha * eps2_{t-1} + beta * sigma2_{t-1}``,
    seeded at the unconditional variance ``omega / (1 - alpha - beta)``.
    Generated independently of the ``arch`` package so the test data cannot
    inherit a bug from the library under test.

    Innovations are standard normal (``dist='normal'``, the default — output
    is bit-for-bit identical to before the option existed) or standardised
    Student-t (``dist='t'`` with ``nu > 2``, scaled by ``sqrt(nu/(nu-2))`` to
    unit variance so the unconditional variance formula is unchanged and only
    the tail weight differs — the fat-tailed known-answer case).

    True parameters: ``omega``, ``alpha`` (ARCH), ``beta`` (GARCH) and, for
    the t variant, the innovation degrees of freedom ``nu``. Downstream tests
    rely on pronounced volatility clustering (ARCH-LM rejects) and on the
    parameters being recoverable by GARCH estimation.
    """
    if alpha + beta >= 1:
        raise ValueError(f"GARCH(1,1) needs alpha + beta < 1, got {alpha + beta}")
    rng = np.random.default_rng(seed)
    total = n + burn_in
    if dist == "normal":
        z = rng.standard_normal(total)
    elif dist == "t":
        if nu is None or nu <= 2:
            raise ValueError(f"dist='t' needs nu > 2 for a finite variance, got nu={nu}")
        z = rng.standard_t(nu, total) / np.sqrt(nu / (nu - 2.0))
    else:
        raise ValueError(f"unknown innovation dist {dist!r}; use 'normal' or 't'")
    returns = np.empty(total)
    sigma2 = omega / (1.0 - alpha - beta)
    for t in range(total):
        eps = np.sqrt(sigma2) * z[t]
        returns[t] = eps
        sigma2 = omega + alpha * eps**2 + beta * sigma2
    return pd.Series(returns[burn_in:], index=_bdays(n), name="garch")


def make_cointegrated_pair(n: int, seed: int) -> tuple[pd.Series, pd.Series]:
    """A cointegrated pair: ``x`` is a random walk, ``y = 1.5 * x + noise``.

    The noise is a stationary AR(1) (phi = 0.5, unit innovation variance), so
    the spread ``y - 1.5 * x`` is stationary while each series alone is I(1).

    True parameter: cointegrating vector (1, -1.5). Downstream tests rely on
    the shared business-day index and on exactly one cointegrating relation.
    """
    rng = np.random.default_rng(seed)
    idx = _bdays(n)
    x = pd.Series(np.cumsum(rng.normal(0.0, 1.0, n)), index=idx, name="x")
    phi = 0.5
    eps = rng.normal(0.0, 1.0, n)
    noise = np.empty(n)
    noise[0] = eps[0]
    for t in range(1, n):
        noise[t] = phi * noise[t - 1] + eps[t]
    y = pd.Series(1.5 * x.to_numpy() + noise, index=idx, name="y")
    return x, y
