"""Multi-factor asset pricing models: Fama-French 3/5 and Carhart 4.

One shared OLS implementation; three registrations that differ only in the
default factor list bound into their params models.
"""

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


class FactorModelParams(BaseModel):
    """Column bindings shared by every named factor model."""

    asset: str = Field(default="asset", description="Column of per-period asset returns.")
    factors: list[str] = Field(description="Factor return columns (already excess/long-short).")
    risk_free: str | None = Field(
        default=None,
        description="Optional per-period risk-free column subtracted from the asset"
        " returns only; factor returns are assumed to already be excess.",
    )
    frequency: Literal["D", "W", "M", "Q", "A"] = Field(
        default="D", description="Return frequency, used to annualise alpha."
    )
    min_obs: int = Field(default=30, ge=3, description="Minimum aligned observations required.")


class Ff3Params(FactorModelParams):
    factors: list[str] = ["mkt_rf", "smb", "hml"]


class Carhart4Params(FactorModelParams):
    factors: list[str] = ["mkt_rf", "smb", "hml", "mom"]


class Ff5Params(FactorModelParams):
    factors: list[str] = ["mkt_rf", "smb", "hml", "rmw", "cma"]


def _fit_factor_model(data: pd.DataFrame, p: FactorModelParams, *, tool: str) -> ResultSet:
    if not p.factors:
        raise ValueError(f"{tool}: the factors list must not be empty")
    if len(set(p.factors)) != len(p.factors):
        raise ValueError(f"{tool}: duplicate factor columns in {p.factors}")

    columns = [p.asset, *p.factors] + ([p.risk_free] if p.risk_free else [])
    require_columns(data, columns, tool=tool)

    aligned = align_series({c: data[c] for c in columns})
    required = max(p.min_obs, len(p.factors) + 2)
    if len(aligned) < required:
        raise ValueError(
            f"{tool}: needs at least {required} aligned observations, got {len(aligned)};"
            " supply more data or lower min_obs"
        )

    y = aligned[p.asset]
    if p.risk_free is not None:
        y = y - aligned[p.risk_free]

    design = sm.add_constant(aligned[p.factors].to_numpy())
    fit = sm.OLS(y.to_numpy(), design).fit()

    estimates = estimates_from_ols(fit, ["alpha", *p.factors])
    alpha = estimates[0].value
    residuals = pd.Series(fit.resid, index=aligned.index)

    return ResultSet(
        tool=tool,
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=ols_residual_diagnostics(fit.resid),
        scalars={
            "r_squared": float(fit.rsquared),
            "r_squared_adj": float(fit.rsquared_adj),
            "nobs": float(fit.nobs),
            "alpha_annualised": annualise_return(alpha, PERIODS_PER_YEAR[p.frequency]),
        },
        series={"residuals": series_from("residuals", residuals)},
        manifest=build_manifest(data, p, tool=tool, version=_VERSION, libraries=_LIBRARIES),
    )


@get_registry().register(
    name="ff3",
    version=_VERSION,
    family="pricing",
    summary="Fama-French three-factor regression (mkt_rf, smb, hml): alpha and"
    " factor loadings by OLS with residual diagnostics.",
    params_model=Ff3Params,
    preconditions=(
        "asset column contains per-period returns; factor columns are excess/long-short returns",
        "observations share a calendar; misaligned or NaN rows are dropped",
    ),
)
def ff3(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    return _fit_factor_model(data, coerce_params(params, Ff3Params), tool="ff3")


@get_registry().register(
    name="carhart4",
    version=_VERSION,
    family="pricing",
    summary="Carhart four-factor regression (mkt_rf, smb, hml, mom): alpha and"
    " factor loadings by OLS with residual diagnostics.",
    params_model=Carhart4Params,
    preconditions=(
        "asset column contains per-period returns; factor columns are excess/long-short returns",
        "observations share a calendar; misaligned or NaN rows are dropped",
    ),
)
def carhart4(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    return _fit_factor_model(data, coerce_params(params, Carhart4Params), tool="carhart4")


@get_registry().register(
    name="ff5",
    version=_VERSION,
    family="pricing",
    summary="Fama-French five-factor regression (mkt_rf, smb, hml, rmw, cma):"
    " alpha and factor loadings by OLS with residual diagnostics.",
    params_model=Ff5Params,
    preconditions=(
        "asset column contains per-period returns; factor columns are excess/long-short returns",
        "observations share a calendar; misaligned or NaN rows are dropped",
    ),
)
def ff5(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    return _fit_factor_model(data, coerce_params(params, Ff5Params), tool="ff5")
