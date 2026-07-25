"""Gibbons-Ross-Shanken (1989) joint test that all portfolio alphas are zero.

Implemented directly from the per-portfolio OLS alphas and the MLE residual
covariance rather than via linearmodels' TradedFactorModel: the J-statistic is
the asymptotic chi-square variant, while the GRS statistic is the exact
finite-sample test with an explicit F(N, T - N - K) distribution — which the
result must expose. The test suite cross-checks this implementation against
the J-statistic.

GRS = ((T - N - K) / N) * (alpha' Sigma^-1 alpha) / (1 + mu' Omega^-1 mu)

with T observations, N portfolios, K traded factors, alpha the OLS intercepts,
Sigma the MLE residual covariance, mu the factor means and Omega the MLE
factor covariance.
"""

import numpy as np
import pandas as pd
import statsmodels.api as sm
from pydantic import BaseModel, Field
from scipy import stats

from econometrica.econ.pricing._common import (
    build_manifest,
    coerce_params,
    estimates_from_ols,
    require_columns,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.returns import align_series
from econometrica.econ.types import Diagnostic, Estimate, ResultSet

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "scipy", "statsmodels")


class GrsParams(BaseModel):
    """Column bindings for the GRS test."""

    portfolios: list[str] = Field(
        description="Excess-return columns of the test portfolios (the alphas under test)."
    )
    factors: list[str] = Field(
        default=["mkt_rf"],
        description="Excess-return columns of the traded factors.",
    )


@get_registry().register(
    name="grs_test",
    version=_VERSION,
    family="pricing",
    summary="Gibbons-Ross-Shanken exact F test that all portfolio alphas are"
    " jointly zero against a set of traded factors.",
    params_model=GrsParams,
    preconditions=(
        "portfolio and factor columns contain per-period excess returns",
        "factors are traded (excess returns), as the GRS test requires",
    ),
)
def grs_test(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, GrsParams)
    if not p.portfolios:
        raise ValueError("grs_test: the portfolios list must not be empty")
    if not p.factors:
        raise ValueError("grs_test: the factors list must not be empty")
    overlap = set(p.portfolios) & set(p.factors)
    if overlap:
        raise ValueError(f"grs_test: columns {sorted(overlap)} appear as both portfolio and factor")
    require_columns(data, [*p.factors, *p.portfolios], tool="grs_test")

    aligned = align_series({c: data[c] for c in [*p.factors, *p.portfolios]})
    t_obs = len(aligned)
    n_port = len(p.portfolios)
    k_fac = len(p.factors)
    if t_obs < n_port + k_fac + 2:
        raise ValueError(
            f"grs_test: needs at least {n_port + k_fac + 2} aligned observations for"
            f" {n_port} portfolios on {k_fac} factor(s), got {t_obs}"
        )

    factor_matrix = aligned[p.factors].to_numpy()
    design = sm.add_constant(factor_matrix)

    alphas = np.empty(n_port)
    residuals = np.empty((t_obs, n_port))
    estimates: list[Estimate] = []
    for j, portfolio in enumerate(p.portfolios):
        fit = sm.OLS(aligned[portfolio].to_numpy(), design).fit()
        alphas[j] = fit.params[0]
        residuals[:, j] = fit.resid
        estimates.append(estimates_from_ols(fit, [f"alpha_{portfolio}"])[0])

    sigma = residuals.T @ residuals / t_obs  # MLE residual covariance
    mu = factor_matrix.mean(axis=0)
    omega = np.atleast_2d(np.cov(factor_matrix, rowvar=False, ddof=0))

    alpha_quad = float(alphas @ np.linalg.solve(sigma, alphas))
    sharpe_quad = float(mu @ np.linalg.solve(omega, mu))
    df1 = n_port
    df2 = t_obs - n_port - k_fac
    grs_stat = (df2 / df1) * alpha_quad / (1.0 + sharpe_quad)
    p_value = float(stats.f.sf(grs_stat, df1, df2))

    diagnostic = Diagnostic(
        name="grs_f",
        statistic=float(grs_stat),
        p_value=p_value,
        critical_values={
            "5%": float(stats.f.ppf(0.95, df1, df2)),
            "1%": float(stats.f.ppf(0.99, df1, df2)),
        },
        passed=bool(p_value >= 0.05),
        interpretation=f"Joint H0: all {n_port} alphas are zero; exact F({df1}, {df2})"
        " under normality. passed means the factor model is not rejected at 5%.",
    )

    return ResultSet(
        tool="grs_test",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=[diagnostic],
        scalars={
            "grs_stat": float(grs_stat),
            "grs_p_value": p_value,
            "df1": float(df1),
            "df2": float(df2),
            "nobs": float(t_obs),
            "n_portfolios": float(n_port),
            "n_factors": float(k_fac),
        },
        manifest=build_manifest(data, p, tool="grs_test", version=_VERSION, libraries=_LIBRARIES),
    )
