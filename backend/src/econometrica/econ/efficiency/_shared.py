"""Series preparation shared by the market efficiency tool family.

Every efficiency tool tests a single series drawn from one column of the
supplied frame. ``Transform`` describes how to turn that column into the
series under test; each tool documents which options make sense for it
(levels for the unit root and variance ratio tools, returns for the
randomness tools).
"""

import math
from typing import Literal

import numpy as np
import pandas as pd

from econometrica.econ._common import require_columns

Transform = Literal["none", "log", "diff", "log_diff", "cumsum"]

TRANSFORM_FIELD_DOC = (
    "Applied to the column before testing: 'none' uses it as-is, 'log' takes"
    " logs (levels of strictly positive prices), 'diff' first-differences,"
    " 'log_diff' takes log returns (strictly positive prices), 'cumsum'"
    " cumulates a return column into a level path."
)


def schwert_lags(nobs: int) -> int:
    """Schwert's (1989) deterministic lag rule: ``floor(12 * (n/100)^(1/4))``.

    Used as the default lag/bandwidth for all three unit root tools so results
    never depend on the library's data-dependent selection, which both changes
    across library versions and emits deprecation warnings under arch 8.
    """
    if nobs <= 0:
        raise ValueError(f"nobs must be positive, got {nobs}")
    scaled: float = 12.0 * (nobs / 100.0) ** 0.25
    return math.floor(scaled)


def prepare_series(
    data: pd.DataFrame,
    *,
    column: str,
    transform: Transform,
    min_obs: int,
    tool: str,
) -> pd.Series:
    """Extract, transform and validate the series under test.

    NaNs are dropped before transforming. Log transforms refuse non-positive
    values loudly rather than emitting NaN/inf into a test statistic. The
    minimum-observation check runs on the transformed series — what the test
    actually sees.
    """
    require_columns(data, [column], tool=tool)
    series = data[column].dropna().astype(float)

    if transform in ("log", "log_diff") and not (series > 0).all():
        raise ValueError(
            f"{tool}: transform {transform!r} requires strictly positive values in"
            f" column {column!r}; use 'none'/'diff'/'cumsum' for series that can be"
            " non-positive"
        )

    if transform == "log":
        series = pd.Series(np.log(series.to_numpy()), index=series.index)
    elif transform == "diff":
        series = series.diff().dropna()
    elif transform == "log_diff":
        series = pd.Series(np.log(series.to_numpy()), index=series.index).diff().dropna()
    elif transform == "cumsum":
        series = series.cumsum()

    if len(series) < min_obs:
        raise ValueError(
            f"{tool}: needs at least {min_obs} observations after the"
            f" {transform!r} transform, got {len(series)}; supply more data or"
            " lower min_obs"
        )
    return series
