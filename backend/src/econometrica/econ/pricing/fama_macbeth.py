"""Fama-MacBeth two-pass cross-sectional risk premium estimation.

Wraps :class:`linearmodels.panel.FamaMacBeth`: per-period cross-sectional
regressions of returns on factor exposures, premiums averaged over time with
Fama-MacBeth standard errors.
"""

import numpy as np
import pandas as pd
from linearmodels.panel import FamaMacBeth
from pydantic import BaseModel, Field

from econometrica.econ.pricing._common import (
    build_manifest,
    coerce_params,
    require_columns,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Estimate, ResultSet

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "linearmodels")


class FamaMacBethParams(BaseModel):
    """Column bindings for the (entity, date) panel."""

    returns_col: str = Field(
        default="returns", description="Column of per-period entity returns."
    )
    exposures: list[str] = Field(
        default=["exposure"],
        description="Factor exposure (characteristic) columns; one risk premium"
        " is estimated per exposure.",
    )


@get_registry().register(
    name="fama_macbeth",
    version=_VERSION,
    family="pricing",
    summary="Fama-MacBeth two-pass regression: per-period cross-sections of"
    " returns on factor exposures, averaged into risk premiums with"
    " Fama-MacBeth standard errors.",
    params_model=FamaMacBethParams,
    preconditions=(
        "data is a panel with a two-level (entity, date) MultiIndex",
        "exposure columns are pre-estimated factor exposures or characteristics",
    ),
)
def fama_macbeth(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, FamaMacBethParams)

    if data.index.nlevels != 2:
        raise ValueError(
            "fama_macbeth: expects a panel with a two-level (entity, date) MultiIndex,"
            f" got an index with {data.index.nlevels} level(s)"
        )
    if not p.exposures:
        raise ValueError("fama_macbeth: the exposures list must not be empty")
    if len(set(p.exposures)) != len(p.exposures):
        raise ValueError(f"fama_macbeth: duplicate exposure columns in {p.exposures}")
    require_columns(data, [p.returns_col, *p.exposures], tool="fama_macbeth")

    panel = data[[p.returns_col, *p.exposures]].dropna()
    n_entities = panel.index.get_level_values(0).nunique()
    n_periods = panel.index.get_level_values(1).nunique()
    if n_periods < 2:
        raise ValueError(
            f"fama_macbeth: needs at least 2 time periods to average premiums,"
            f" got {n_periods}"
        )
    if n_entities < len(p.exposures) + 2:
        raise ValueError(
            f"fama_macbeth: needs at least {len(p.exposures) + 2} entities per"
            f" cross-section for {len(p.exposures)} exposure(s), got {n_entities}"
        )

    exog = panel[p.exposures].copy()
    exog.insert(0, "const", 1.0)
    fit = FamaMacBeth(panel[p.returns_col], exog).fit()

    ci = fit.conf_int()
    estimates = [
        Estimate(
            name=name,
            value=float(fit.params[name]),
            std_error=float(fit.std_errors[name]),
            t_stat=float(fit.tstats[name]),
            p_value=float(fit.pvalues[name]),
            ci_low=float(ci.loc[name, "lower"]),
            ci_high=float(ci.loc[name, "upper"]),
        )
        for name in ["const", *p.exposures]
    ]

    return ResultSet(
        tool="fama_macbeth",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        scalars={
            "nobs": float(len(panel)),
            "n_entities": float(n_entities),
            "n_periods": float(n_periods),
            "r_squared": float(np.asarray(fit.rsquared, dtype=float)),
        },
        manifest=build_manifest(
            data, p, tool="fama_macbeth", version=_VERSION, libraries=_LIBRARIES
        ),
    )
