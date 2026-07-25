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
    *, omega: float, alpha: float, beta: float, n: int, seed: int, burn_in: int = 500
) -> pd.Series:
    """Zero-mean returns from a GARCH(1,1) iterated directly, not via ``arch``.

    Recursion: ``sigma2_t = omega + alpha * eps2_{t-1} + beta * sigma2_{t-1}``
    with Gaussian innovations, seeded at the unconditional variance
    ``omega / (1 - alpha - beta)``. Generated independently of the ``arch``
    package so the test data cannot inherit a bug from the library under test.

    True parameters: ``omega``, ``alpha`` (ARCH), ``beta`` (GARCH). Downstream
    tests rely on pronounced volatility clustering (ARCH-LM rejects) and on
    the parameters being recoverable by GARCH estimation.
    """
    if alpha + beta >= 1:
        raise ValueError(f"GARCH(1,1) needs alpha + beta < 1, got {alpha + beta}")
    rng = np.random.default_rng(seed)
    total = n + burn_in
    z = rng.standard_normal(total)
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
