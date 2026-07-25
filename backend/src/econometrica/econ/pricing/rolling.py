"""Rolling market beta with a confidence band, via statsmodels RollingOLS."""

import numpy as np
import pandas as pd
import statsmodels.api as sm
from pydantic import BaseModel, Field
from scipy import stats
from statsmodels.regression.rolling import RollingOLS

from econometrica.econ._common import (
    build_manifest,
    coerce_params,
    iso_index,
    require_columns,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.returns import align_series
from econometrica.econ.types import ResultSet, Series

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "scipy", "statsmodels")


class RollingBetaParams(BaseModel):
    """Column bindings and options for the rolling beta estimation."""

    asset: str = Field(default="asset", description="Column of per-period asset returns.")
    market: str = Field(default="market", description="Column of per-period market returns.")
    window: int = Field(default=252, ge=20, description="Rolling window length in observations.")
    ci_level: float = Field(
        default=0.95, gt=0.0, lt=1.0, description="Confidence level for the beta band."
    )


@get_registry().register(
    name="rolling_beta",
    version=_VERSION,
    family="pricing",
    summary="Rolling-window OLS market beta with a confidence band, for tracking"
    " time-varying systematic risk.",
    params_model=RollingBetaParams,
    preconditions=(
        "asset and market columns contain per-period returns (not prices)",
        "the aligned sample is at least one window long",
    ),
)
def rolling_beta(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, RollingBetaParams)
    require_columns(data, [p.asset, p.market], tool="rolling_beta")

    aligned = align_series({p.asset: data[p.asset], p.market: data[p.market]})
    if p.window > len(aligned):
        raise ValueError(
            f"rolling_beta: window ({p.window}) exceeds the {len(aligned)} aligned"
            " observations; shrink the window or supply more data"
        )

    exog = sm.add_constant(aligned[[p.market]])
    fit = RollingOLS(aligned[p.asset], exog, window=p.window).fit()

    betas = fit.params[p.market].dropna()
    ses = fit.bse[p.market].reindex(betas.index)
    t_crit = float(stats.t.ppf(0.5 + p.ci_level / 2.0, df=p.window - 2))
    ci_low = betas - t_crit * ses
    ci_high = betas + t_crit * ses

    x = iso_index(betas.index)
    beta_values = betas.to_numpy()

    return ResultSet(
        tool="rolling_beta",
        version=_VERSION,
        params=p.model_dump(),
        scalars={
            "window": float(p.window),
            "nobs": float(len(aligned)),
            "beta_mean": float(np.mean(beta_values)),
            "beta_min": float(np.min(beta_values)),
            "beta_max": float(np.max(beta_values)),
        },
        series={
            "beta": Series(name="beta", x=x, y=[float(v) for v in beta_values]),
            "beta_ci_low": Series(name="beta_ci_low", x=x, y=[float(v) for v in ci_low]),
            "beta_ci_high": Series(name="beta_ci_high", x=x, y=[float(v) for v in ci_high]),
        },
        manifest=build_manifest(
            data, p, tool="rolling_beta", version=_VERSION, libraries=_LIBRARIES
        ),
    )
