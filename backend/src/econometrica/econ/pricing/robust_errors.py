"""Shared covariance options for the pricing family's OLS tools.

Not a registered tool: CAPM and the factor models expose a
``cov``/``hac_lags`` pair in their params and delegate the fitting here so the
mapping to statsmodels covariance types exists exactly once. Robust covariance
changes standard errors, t-stats, p-values and confidence intervals only —
point estimates are untouched by construction.
"""

import math
from typing import Any, Literal

import numpy as np
import statsmodels.api as sm

CovType = Literal["nonrobust", "white", "newey_west"]


def newey_west_default_lags(nobs: int) -> int:
    """Newey-West automatic lag rule: ``floor(4 * (n/100)^(2/9))``."""
    if nobs <= 0:
        raise ValueError(f"nobs must be positive, got {nobs}")
    scaled: float = 4.0 * (nobs / 100.0) ** (2.0 / 9.0)
    return math.floor(scaled)


def fit_ols(
    y: np.ndarray,
    design: np.ndarray,
    *,
    cov: CovType = "nonrobust",
    hac_lags: int | None = None,
) -> tuple[Any, int | None]:
    """Fit OLS with the requested covariance estimator.

    Returns the statsmodels fit — internal only, it must never leak past the
    tool boundary — and the HAC lag count actually used (None unless
    ``cov="newey_west"``, where a None ``hac_lags`` applies the automatic rule).
    """
    model = sm.OLS(y, design)
    if cov == "nonrobust":
        return model.fit(), None
    if cov == "white":
        return model.fit(cov_type="HC3"), None
    if cov == "newey_west":
        lags = hac_lags if hac_lags is not None else newey_west_default_lags(len(y))
        return model.fit(cov_type="HAC", cov_kwds={"maxlags": lags}), lags
    raise ValueError(f"unknown covariance type: {cov!r}")
