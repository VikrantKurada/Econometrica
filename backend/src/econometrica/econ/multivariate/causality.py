"""Granger causality between two series.

Wraps ``statsmodels.tsa.stattools.grangercausalitytests`` — whose deprecated
``verbose`` argument is never passed; the tool consumes the returned dict and
redirects the function's legacy default printing to a throwaway buffer.

Granger causality is PREDICTIVE, not structural: x "Granger-causes" y when
lagged x improves the prediction of y beyond lagged y alone. The per-direction
summary diagnostic takes the smallest p-value across lags 1..maxlag WITHOUT a
multiple-testing adjustment (documented in its interpretation); the per-lag
table is the evidence to inspect before concluding anything.
"""

import contextlib
import io
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import build_manifest, coerce_params
from econometrica.econ.multivariate._shared import prepare_frame
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet, Table

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "statsmodels")

_Statistic = Literal["ssr_ftest", "ssr_chi2test", "lrtest", "params_ftest"]


class GrangerCausalityParams(BaseModel):
    """Options for the pairwise Granger causality tool."""

    x: str = Field(default="x", description="First series column.")
    y: str = Field(default="y", description="Second series column.")
    maxlag: int = Field(
        default=5, ge=1, description="Test at every lag from 1 to maxlag."
    )
    direction: Literal["both", "x_to_y", "y_to_x"] = Field(
        default="both",
        description="Which causal direction(s) to test; 'both' tests x->y and y->x.",
    )
    statistic: _Statistic = Field(
        default="ssr_ftest",
        description="Test statistic reported: SSR-based F (default), SSR-based"
        " chi2, likelihood ratio, or parameter F.",
    )
    min_obs: int = Field(
        default=50, ge=30, description="Minimum complete observations required."
    )


@get_registry().register(
    name="granger_causality",
    version=_VERSION,
    family="multivariate",
    summary="Pairwise Granger (predictive) causality tests at every lag from 1"
    " to maxlag, both directions by default; each direction gets a per-lag"
    " table and a min-p summary diagnostic whose multiple-testing caveat is"
    " spelled out in its interpretation.",
    params_model=GrangerCausalityParams,
    preconditions=(
        "both columns hold stationary series (returns or differences, not price levels)",
        "rows with a NaN in either column are dropped",
    ),
)
def granger_causality(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from statsmodels.tsa.stattools import grangercausalitytests

    p = coerce_params(params, GrangerCausalityParams)
    frame = prepare_frame(data, [p.x, p.y], min_obs=p.min_obs, tool="granger_causality")
    if len(frame) < p.min_obs + 4 * p.maxlag:
        raise ValueError(
            f"granger_causality: needs at least {p.min_obs + 4 * p.maxlag}"
            f" complete observations for maxlag={p.maxlag}, got {len(frame)};"
            " supply more data or lower maxlag"
        )

    directions: list[tuple[str, str]] = []  # (causing, caused)
    if p.direction in ("both", "x_to_y"):
        directions.append((p.x, p.y))
    if p.direction in ("both", "y_to_x"):
        directions.append((p.y, p.x))

    diagnostics: list[Diagnostic] = []
    rows: list[list[Any]] = []
    for causing, caused in directions:
        arr = np.column_stack([frame[caused].to_numpy(), frame[causing].to_numpy()])
        # grangercausalitytests still PRINTS by default and its verbose flag is
        # deprecated (warns when passed), so the legacy output goes to a
        # throwaway buffer and only the returned dict is consumed.
        with contextlib.redirect_stdout(io.StringIO()):
            results = grangercausalitytests(arr, maxlag=p.maxlag)

        label = f"{causing}->{caused}"
        by_lag: dict[int, tuple[float, float]] = {}
        for lag, (tests, _fits) in results.items():
            stat, p_value = float(tests[p.statistic][0]), float(tests[p.statistic][1])
            by_lag[int(lag)] = (stat, p_value)
            rows.append([label, float(lag), stat, p_value])

        best_lag = min(by_lag, key=lambda lag: by_lag[lag][1])
        best_stat, best_p = by_lag[best_lag]
        diagnostics.append(
            Diagnostic(
                name=f"granger_{causing}_to_{caused}",
                statistic=best_stat,
                p_value=best_p,
                passed=bool(best_p < 0.05),
                interpretation=f"H0: {causing} does not Granger-cause {caused}"
                " (lagged values add no predictive power). passed means H0 is"
                f" rejected at 5%. Statistic and p-value are {p.statistic} at"
                f" lag {best_lag} — the SMALLEST p across lags 1..{p.maxlag}"
                " with NO multiple-testing adjustment, so treat this summary"
                " as exploratory and inspect the per-lag table. Predictive,"
                " not structural, causality.",
            )
        )

    return ResultSet(
        tool="granger_causality",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars={"nobs": float(len(frame)), "maxlag": float(p.maxlag)},
        tables={
            "granger": Table(columns=["direction", "lag", "statistic", "p_value"], rows=rows)
        },
        manifest=build_manifest(
            data, p, tool="granger_causality", version=_VERSION, libraries=_LIBRARIES
        ),
    )
