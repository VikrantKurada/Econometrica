"""Shared plumbing for the econ tool families.

Started life inside the pricing family and was promoted here on second use
(the efficiency family). Nothing here is a registered tool; these helpers keep
the tool modules focused on the econometrics. Library result objects consumed
here never escape: every public surface of the families speaks
:class:`~econometrica.econ.types.ResultSet`.
"""

from collections.abc import Iterable, Sequence
from importlib import metadata
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from econometrica.econ.fingerprint import fingerprint_frame, fingerprint_params
from econometrica.econ.types import Diagnostic, Estimate, Manifest, Series

Transform = Literal["none", "log", "diff", "log_diff", "cumsum"]

TRANSFORM_FIELD_DOC = (
    "Applied to the column before testing: 'none' uses it as-is, 'log' takes"
    " logs (levels of strictly positive prices), 'diff' first-differences,"
    " 'log_diff' takes log returns (strictly positive prices), 'cumsum'"
    " cumulates a return column into a level path."
)


def coerce_params[P: BaseModel](params: BaseModel, model: type[P]) -> P:
    """Narrow the registry's ``BaseModel`` to the tool's own params model."""
    if isinstance(params, model):
        return params
    return model.model_validate(params.model_dump())


def require_columns(data: pd.DataFrame, columns: Iterable[str], *, tool: str) -> None:
    missing = [c for c in columns if c not in data.columns]
    if missing:
        raise ValueError(
            f"{tool}: missing column(s) {missing} in the supplied data; "
            f"available columns are {list(data.columns)}"
        )


def prepare_series(
    data: pd.DataFrame,
    *,
    column: str,
    transform: Transform,
    min_obs: int,
    tool: str,
) -> pd.Series:
    """Extract, transform and validate the series a tool operates on.

    Promoted from the efficiency family on third use (the volatility family).
    NaNs are dropped before transforming. Log transforms refuse non-positive
    values loudly rather than emitting NaN/inf into a test statistic. The
    minimum-observation check runs on the transformed series — what the tool
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


def build_manifest(
    data: pd.DataFrame,
    params: BaseModel,
    *,
    tool: str,
    version: str,
    libraries: Sequence[str],
) -> Manifest:
    """Fingerprint exactly what the tool received: the raw frame and the params."""
    return Manifest(
        data_fingerprint=fingerprint_frame(data),
        tool=tool,
        tool_version=version,
        params_hash=fingerprint_params(params.model_dump()),
        library_versions={lib: metadata.version(lib) for lib in libraries},
    )


def iso_index(index: pd.Index) -> list[str]:
    """Render an index as plot-ready x values (ISO dates for datetime indexes)."""
    if isinstance(index, pd.DatetimeIndex):
        return [str(x) for x in index.strftime("%Y-%m-%d")]
    return [str(x) for x in index]


def series_from(name: str, values: pd.Series) -> Series:
    y = [float(v) if np.isfinite(v) else None for v in values.to_numpy()]
    return Series(name=name, x=iso_index(values.index), y=y)


def estimates_from_ols(fit: Any, names: Sequence[str]) -> list[Estimate]:
    """Map a statsmodels OLS fit onto plain :class:`Estimate` records.

    ``names`` must be ordered like the fit's design matrix columns.
    """
    ci = np.asarray(fit.conf_int())
    return [
        Estimate(
            name=names[i],
            value=float(fit.params[i]),
            std_error=float(fit.bse[i]),
            t_stat=_finite_or_none(fit.tvalues[i]),
            p_value=_finite_or_none(fit.pvalues[i]),
            ci_low=_finite_or_none(ci[i, 0]),
            ci_high=_finite_or_none(ci[i, 1]),
        )
        for i in range(len(names))
    ]


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def ols_residual_diagnostics(residuals: np.ndarray) -> list[Diagnostic]:
    """The two residual checks every OLS-based tool reports inline.

    Delegates to the diagnostics engine so the codebase carries exactly one
    Jarque-Bera and one Durbin-Watson convention — including the 1.5-2.5 band,
    which previously lived here as a second copy. Tools call this for their
    always-on checks; the orchestrator calls
    :func:`econometrica.econ.diagnostics.run_diagnostics` for the full battery.

    Degenerate (zero-variance) residuals leave ``passed`` as None — "not
    judged", never "failed".
    """
    from econometrica.econ.diagnostics import durbin_watson_check, jarque_bera_check

    return [jarque_bera_check(residuals), durbin_watson_check(residuals)]
