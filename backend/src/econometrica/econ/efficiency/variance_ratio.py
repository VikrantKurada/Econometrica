"""Lo-MacKinlay (1988) variance ratio test over a set of horizons.

Wraps ``arch.unitroot.VarianceRatio`` rather than reimplementing the test:
that keeps the whole efficiency family on one library (versioned in every
manifest) and avoids hand-rolling the heteroskedasticity-robust asymptotic
variance, which is easy to get subtly wrong. Each horizon is fitted twice —
``robust=False`` for the homoskedastic z and ``robust=True`` for the
het-robust z — with the horizon passed explicitly as ``lags`` so nothing is
data-dependent. The overlapping, debiased estimator (arch's defaults) is used.

Input semantics: arch differences the input internally, so this tool tests a
LEVEL path. Pass log prices directly (``transform='none'``), raw prices via
``transform='log'``, or a return column via ``transform='cumsum'``. VR is
invariant to the level path's additive constant, so cumulated returns
reproduce the original level statistics exactly.

The horizon-1 row (VR = 1 by construction, no test statistic) is always
included so charts and tables anchor at the definitional value.
"""

import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import build_manifest, coerce_params
from econometrica.econ.efficiency._shared import TRANSFORM_FIELD_DOC, Transform, prepare_series
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet, Series, Table

_VERSION = "1.0.0"
_LIBRARIES = ("arch", "numpy", "pandas")


class VarianceRatioParams(BaseModel):
    """Column binding and horizons for the variance ratio test."""

    column: str = Field(
        default="price",
        description="Column holding the level path under test (typically log prices).",
    )
    transform: Transform = Field(
        default="none",
        description=TRANSFORM_FIELD_DOC
        + " The transformed series must be a level path: use 'log' for raw"
        " prices and 'cumsum' for a column of returns.",
    )
    horizons: list[int] = Field(
        default=[2, 4, 8, 16],
        description="Aggregation horizons q to test (each >= 2; horizon 1 is"
        " always reported and equals 1 by construction).",
    )
    min_obs: int = Field(
        default=100, ge=50, description="Minimum observations required after transforming."
    )


@get_registry().register(
    name="variance_ratio",
    version=_VERSION,
    family="efficiency",
    summary="Lo-MacKinlay variance ratio test of the random walk hypothesis"
    " over multiple horizons, with homoskedastic and heteroskedasticity-robust"
    " z statistics; VR > 1 signals momentum, VR < 1 mean reversion.",
    params_model=VarianceRatioParams,
    preconditions=(
        "the transformed column is a level path (log prices), not returns",
        "the selected column holds one regularly observed time series; NaNs are dropped",
    ),
)
def variance_ratio(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from arch.unitroot import VarianceRatio

    p = coerce_params(params, VarianceRatioParams)
    if not p.horizons:
        raise ValueError("variance_ratio: the horizons list must not be empty")
    if any(h < 2 for h in p.horizons):
        raise ValueError(
            f"variance_ratio: every horizon must be >= 2, got {sorted(p.horizons)};"
            " horizon 1 is reported automatically and equals 1 by construction"
        )
    horizons = sorted(set(p.horizons))

    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="variance_ratio"
    )
    max_horizon = horizons[-1]
    if len(series) < 2 * max_horizon:
        raise ValueError(
            f"variance_ratio: the largest horizon ({max_horizon}) needs at least"
            f" {2 * max_horizon} observations, got {len(series)}; drop the horizon"
            " or supply more data"
        )

    values = series.to_numpy()
    rows: list[list[object]] = [[1.0, 1.0, None, None, None]]
    vr_values: list[float | None] = [1.0]
    diagnostics: list[Diagnostic] = []
    for q in horizons:
        robust = VarianceRatio(values, lags=q, robust=True)
        homoskedastic = VarianceRatio(values, lags=q, robust=False)
        vr = float(robust.vr)
        z_het = float(robust.stat)
        p_value = float(robust.pvalue)
        rows.append([float(q), vr, float(homoskedastic.stat), z_het, p_value])
        vr_values.append(vr)
        diagnostics.append(
            Diagnostic(
                name=f"vr_h{q}",
                statistic=z_het,
                p_value=p_value,
                passed=bool(p_value >= 0.05),
                interpretation=f"H0: VR({q}) = 1 (random walk), het-robust z."
                " passed means the random-walk null is not rejected at 5%."
                " VR > 1 signals positive autocorrelation (momentum), VR < 1"
                " mean reversion.",
            )
        )

    vr_series = Series(
        name="vr_by_horizon", x=[str(q) for q in [1, *horizons]], y=vr_values
    )

    return ResultSet(
        tool="variance_ratio",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars={"nobs": float(len(series)), "n_horizons": float(len(horizons))},
        tables={
            "variance_ratios": Table(
                columns=["horizon", "vr", "z_homoskedastic", "z_heteroskedastic", "p_value"],
                rows=rows,
            )
        },
        series={"vr_by_horizon": vr_series},
        manifest=build_manifest(
            data, p, tool="variance_ratio", version=_VERSION, libraries=_LIBRARIES
        ),
    )
