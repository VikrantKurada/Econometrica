"""Vector autoregression tools: var_model, irf and fevd.

All three share one estimation path over ``statsmodels.tsa.api.VAR``: the
system is fitted on the selected columns with a constant term, and the lag
order is chosen by an information criterion up to ``maxlags`` (or fixed at
``maxlags`` when ``ic`` is None). ``irf`` and ``fevd`` REFIT the VAR from the
same spec params rather than receiving a fitted object — tools only exchange
:class:`~econometrica.econ.types.ResultSet`, never library results — so their
params models embed the full VAR spec and the refit is documented in the
registry summaries.

Coefficient naming: ``<equation>.L<lag>.<regressor>`` — ``y1.L1.y2`` is the
coefficient on lag-1 ``y2`` in the ``y1`` equation; the intercept is
``<equation>.const``. Confidence intervals are the standard asymptotic-normal
ones (value +/- z_{0.975} * SE).

Orthogonalization (irf/fevd) uses the Cholesky factor of the residual
covariance in COLUMN ORDER: shocks to earlier columns may move later columns
contemporaneously but not vice versa. Reorder ``columns`` to change the
recursive ordering.
"""

import warnings
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats

from econometrica.econ._common import build_manifest, coerce_params
from econometrica.econ.multivariate._shared import COLUMNS_FIELD_DOC, prepare_frame
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, Estimate, ResultSet, Series, Table

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "statsmodels")

_REFIT_NOTE = (
    " Refits the VAR from the embedded spec params (columns/maxlags/ic);"
    " results are deterministic, so the refit reproduces var_model's fit"
    " exactly."
)


class _VarSpecParams(BaseModel):
    """The VAR specification shared by var_model, irf and fevd."""

    columns: list[str] = Field(default_factory=list, description=COLUMNS_FIELD_DOC)
    maxlags: int = Field(
        default=10,
        ge=1,
        description="Maximum lag order considered by the information criterion"
        " (the exact lag order fitted when ic is None).",
    )
    ic: Literal["aic", "bic", "hqic", "fpe"] | None = Field(
        default="aic",
        description="Lag-selection information criterion; None fits exactly maxlags lags.",
    )
    min_obs: int = Field(
        default=50, ge=20, description="Minimum complete observations required."
    )


class VarParams(_VarSpecParams):
    """Options for the var_model tool."""

    whiteness_lags: int = Field(
        default=10,
        ge=2,
        description="Portmanteau lag for the residual whiteness diagnostic;"
        " raised to selected_lag + 1 when the fitted lag order reaches it.",
    )


class IrfParams(_VarSpecParams):
    """Options for the impulse-response tool."""

    horizons: int = Field(default=10, ge=1, description="Response horizons (0..horizons).")
    orthogonalized: bool = Field(
        default=True,
        description="True uses Cholesky-orthogonalized shocks (column order is"
        " the recursive ordering); False uses reduced-form unit shocks.",
    )


class FevdParams(_VarSpecParams):
    """Options for the forecast-error variance decomposition tool."""

    horizons: int = Field(
        default=10, ge=1, description="Forecast horizons (1..horizons steps ahead)."
    )


def _fit_var(
    data: pd.DataFrame, p: _VarSpecParams, *, tool: str
) -> tuple[Any, list[str], pd.DataFrame]:
    """Prepare the frame and fit the VAR; returns (fit, columns, frame).

    The endog is passed as an ndarray: statsmodels' own labels are never
    consumed (names are rebuilt from the column selection), and ndarray input
    sidesteps the date-frequency warnings statsmodels emits for irregular
    DatetimeIndexes (real trading calendars have no fixed frequency).
    """
    from statsmodels.tsa.api import VAR

    frame = prepare_frame(data, p.columns, min_obs=p.min_obs, tool=tool)
    try:
        fit = VAR(frame.to_numpy()).fit(maxlags=p.maxlags, ic=p.ic)
    except Exception as exc:  # statsmodels raises plain errors on degenerate systems
        raise ValueError(
            f"{tool}: VAR estimation failed ({exc}); check that the selected"
            f" columns {list(frame.columns)} hold linearly independent series"
            " with enough observations for maxlags"
        ) from exc
    return fit, [str(c) for c in frame.columns], frame


def _regressor_names(columns: list[str], k_ar: int) -> list[str]:
    """Design-matrix row labels in statsmodels order: const, then lag-major."""
    return ["const"] + [f"L{lag}.{col}" for lag in range(1, k_ar + 1) for col in columns]


def _companion_modulus(coefs: np.ndarray) -> float:
    """Largest eigenvalue modulus of the companion matrix (< 1 means stable)."""
    n_lags, k, _ = coefs.shape
    if n_lags == 0:
        return 0.0
    top = np.hstack(list(coefs))
    if n_lags == 1:
        companion = top
    else:
        eye = np.eye(k * (n_lags - 1))
        companion = np.vstack([top, np.hstack([eye, np.zeros((k * (n_lags - 1), k))])])
    return float(np.max(np.abs(np.linalg.eigvals(companion))))


@get_registry().register(
    name="var_model",
    version=_VERSION,
    family="multivariate",
    summary="Fit a vector autoregression with information-criterion lag"
    " selection; reports every coefficient (named <equation>.L<lag>.<regressor>)"
    " with asymptotic inference, plus stability and residual-whiteness"
    " diagnostics.",
    params_model=VarParams,
    preconditions=(
        "every selected column holds one regularly observed series of the same frequency",
        "rows with a NaN in any selected column are dropped",
    ),
)
def var_model(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, VarParams)
    fit, columns, _frame = _fit_var(data, p, tool="var_model")

    k_ar = int(fit.k_ar)
    names = _regressor_names(columns, k_ar)
    values = np.asarray(fit.params, dtype=float)
    stderr = np.asarray(fit.stderr, dtype=float)
    tvalues = np.asarray(fit.tvalues, dtype=float)
    pvalues = np.asarray(fit.pvalues, dtype=float)
    z95 = float(stats.norm.ppf(0.975))

    estimates = [
        Estimate(
            name=f"{equation}.{regressor}",
            value=float(values[i, j]),
            std_error=float(stderr[i, j]),
            t_stat=float(tvalues[i, j]),
            p_value=float(pvalues[i, j]),
            ci_low=float(values[i, j] - z95 * stderr[i, j]),
            ci_high=float(values[i, j] + z95 * stderr[i, j]),
        )
        for j, equation in enumerate(columns)
        for i, regressor in enumerate(names)
    ]

    modulus = _companion_modulus(np.asarray(fit.coefs, dtype=float))
    diagnostics = [
        Diagnostic(
            name="stability",
            statistic=modulus,
            p_value=None,
            passed=bool(modulus < 1.0),
            interpretation="Largest eigenvalue modulus of the companion matrix."
            " passed means it is below 1 — the VAR is stable/stationary and"
            " impulse responses decay. Not a hypothesis test: no p-value.",
        )
    ]

    whiteness_lags = max(p.whiteness_lags, k_ar + 1)
    wh = fit.test_whiteness(nlags=whiteness_lags, adjusted=False)
    bumped = (
        f" (whiteness_lags raised from {p.whiteness_lags} to exceed the fitted lag order)"
        if whiteness_lags != p.whiteness_lags
        else ""
    )
    wh_p = float(wh.pvalue)
    diagnostics.append(
        Diagnostic(
            name="whiteness",
            statistic=float(wh.test_statistic),
            p_value=wh_p,
            passed=bool(wh_p >= 0.05),
            interpretation="Portmanteau test on the residuals up to lag"
            f" {whiteness_lags}{bumped}. H0: no remaining residual"
            " autocorrelation; passed means H0 is NOT rejected at 5% — the"
            " chosen lag order captured the dynamics.",
        )
    )

    return ResultSet(
        tool="var_model",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=diagnostics,
        scalars={
            "selected_lag": float(k_ar),
            "aic": float(fit.aic),
            "bic": float(fit.bic),
            "hqic": float(fit.hqic),
            "nobs": float(fit.nobs),
        },
        manifest=build_manifest(
            data, p, tool="var_model", version=_VERSION, libraries=_LIBRARIES
        ),
    )


@get_registry().register(
    name="irf",
    version=_VERSION,
    family="multivariate",
    summary="Impulse response functions of a VAR: one series per"
    " impulse->response pair over horizons 0..H, Cholesky-orthogonalized in"
    " column order by default." + _REFIT_NOTE,
    params_model=IrfParams,
    preconditions=(
        "every selected column holds one regularly observed series of the same frequency",
        "column order fixes the Cholesky ordering when orthogonalized",
    ),
)
def irf(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, IrfParams)
    fit, columns, _frame = _fit_var(data, p, tool="irf")

    irf_result = fit.irf(p.horizons)
    # responses[h, i, j] = response of variable i at horizon h to a shock in j.
    responses = np.asarray(
        irf_result.orth_irfs if p.orthogonalized else irf_result.irfs, dtype=float
    )
    x = [str(h) for h in range(p.horizons + 1)]

    series: dict[str, Series] = {}
    rows: list[list[Any]] = []
    for j, impulse in enumerate(columns):
        for i, response in enumerate(columns):
            name = f"{impulse}->{response}"
            series[name] = Series(
                name=name, x=x, y=[float(v) for v in responses[:, i, j]]
            )
    for h in range(p.horizons + 1):
        for j, impulse in enumerate(columns):
            for i, response in enumerate(columns):
                rows.append([float(h), impulse, response, float(responses[h, i, j])])

    return ResultSet(
        tool="irf",
        version=_VERSION,
        params=p.model_dump(),
        scalars={"selected_lag": float(fit.k_ar), "nobs": float(fit.nobs)},
        tables={"irf": Table(columns=["horizon", "impulse", "response", "irf"], rows=rows)},
        series=series,
        manifest=build_manifest(data, p, tool="irf", version=_VERSION, libraries=_LIBRARIES),
    )


@get_registry().register(
    name="fevd",
    version=_VERSION,
    family="multivariate",
    summary="Forecast-error variance decomposition of a VAR: the share of each"
    " variable's h-step forecast-error variance attributable to each"
    " Cholesky-orthogonalized shock (series named shock->variable; shares sum"
    " to 1 per variable and horizon)." + _REFIT_NOTE,
    params_model=FevdParams,
    preconditions=(
        "every selected column holds one regularly observed series of the same frequency",
        "column order fixes the Cholesky ordering",
    ),
)
def fevd(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, FevdParams)
    fit, columns, _frame = _fit_var(data, p, tool="fevd")

    with warnings.catch_warnings():
        # statsmodels' FEVD plotting-oriented internals can emit spurious
        # RuntimeWarnings on exactly-singular shares; the decomposition itself
        # is validated below by the sum-to-one property test.
        warnings.simplefilter("ignore", RuntimeWarning)
        decomp = np.asarray(fit.fevd(p.horizons).decomp, dtype=float)
    # decomp[i, h, j] = share of variable i's (h+1)-step variance due to shock j.
    x = [str(h) for h in range(1, p.horizons + 1)]

    series: dict[str, Series] = {}
    rows: list[list[Any]] = []
    for j, shock in enumerate(columns):
        for i, variable in enumerate(columns):
            name = f"{shock}->{variable}"
            series[name] = Series(name=name, x=x, y=[float(v) for v in decomp[i, :, j]])
    for h in range(p.horizons):
        for i, variable in enumerate(columns):
            for j, shock in enumerate(columns):
                rows.append([float(h + 1), variable, shock, float(decomp[i, h, j])])

    return ResultSet(
        tool="fevd",
        version=_VERSION,
        params=p.model_dump(),
        scalars={"selected_lag": float(fit.k_ar), "nobs": float(fit.nobs)},
        tables={"fevd": Table(columns=["horizon", "variable", "shock", "share"], rows=rows)},
        series=series,
        manifest=build_manifest(data, p, tool="fevd", version=_VERSION, libraries=_LIBRARIES),
    )
