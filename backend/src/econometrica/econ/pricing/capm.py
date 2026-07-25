"""CAPM: OLS of (excess) asset returns on (excess) market returns."""

from typing import Literal

import pandas as pd
import statsmodels.api as sm
from pydantic import BaseModel, Field

from econometrica.econ.pricing._common import (
    build_manifest,
    coerce_params,
    estimates_from_ols,
    ols_residual_diagnostics,
    require_columns,
    series_from,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.returns import PERIODS_PER_YEAR, align_series, annualise_return
from econometrica.econ.types import ResultSet

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "statsmodels")


class CapmParams(BaseModel):
    """Column bindings and options for the CAPM regression."""

    asset: str = Field(default="asset", description="Column of per-period asset returns.")
    market: str = Field(default="market", description="Column of per-period market returns.")
    risk_free: str | None = Field(
        default=None,
        description="Optional column of per-period risk-free returns; when set, the"
        " regression runs on excess returns (asset - rf on market - rf).",
    )
    frequency: Literal["D", "W", "M", "Q", "A"] = Field(
        default="D", description="Return frequency, used to annualise alpha."
    )
    min_obs: int = Field(default=30, ge=3, description="Minimum aligned observations required.")


@get_registry().register(
    name="capm",
    version=_VERSION,
    family="pricing",
    summary="Estimate CAPM alpha and beta by OLS of (excess) asset returns on"
    " (excess) market returns, with residual diagnostics.",
    params_model=CapmParams,
    preconditions=(
        "asset and market columns contain per-period returns (not prices)",
        "observations share a calendar; misaligned or NaN rows are dropped",
    ),
)
def capm(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, CapmParams)
    columns = [p.asset, p.market] + ([p.risk_free] if p.risk_free else [])
    require_columns(data, columns, tool="capm")

    aligned = align_series({c: data[c] for c in columns})
    if len(aligned) < p.min_obs:
        raise ValueError(
            f"capm: needs at least {p.min_obs} aligned observations, got {len(aligned)};"
            " supply more data or lower min_obs"
        )

    y = aligned[p.asset]
    x = aligned[p.market]
    if p.risk_free is not None:
        y = y - aligned[p.risk_free]
        x = x - aligned[p.risk_free]

    design = sm.add_constant(x.to_numpy())
    fit = sm.OLS(y.to_numpy(), design).fit()

    estimates = estimates_from_ols(fit, ["alpha", "beta"])
    alpha = estimates[0].value
    residuals = pd.Series(fit.resid, index=aligned.index)

    return ResultSet(
        tool="capm",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=ols_residual_diagnostics(fit.resid),
        scalars={
            "r_squared": float(fit.rsquared),
            "nobs": float(fit.nobs),
            "alpha_annualised": annualise_return(alpha, PERIODS_PER_YEAR[p.frequency]),
        },
        series={"residuals": series_from("residuals", residuals)},
        manifest=build_manifest(data, p, tool="capm", version=_VERSION, libraries=_LIBRARIES),
    )
