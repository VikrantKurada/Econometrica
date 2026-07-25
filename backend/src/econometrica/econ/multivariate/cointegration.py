"""Cointegration tools: johansen, engle_granger and vecm.

Deterministic-term conventions (documented once here): the ``det`` param maps
to ``coint_johansen``'s ``det_order`` and VECM's ``deterministic`` string as
``none -> (-1, "n")``, ``const -> (0, "co")``, ``trend -> (1, "colo")`` — the
constant/trend live OUTSIDE the cointegration relation, the standard case for
price systems. Rank determination is the sequential trace procedure at 95%:
the selected rank is the first r whose trace statistic fails to exceed its
95% critical value (k if every hypothesis is rejected).

Inference caveats, by tool:

- ``engle_granger`` reports the cointegrating OLS coefficients WITH their OLS
  standard errors, but on I(1) regressors those are not asymptotically
  standard — treat them as descriptive and use ``vecm`` for likelihood-based
  inference on the cointegrating vector.
- ``vecm`` normalizes beta so the leading r x r block is the identity
  (Phillips normalization); normalized elements are fixed, not estimated, and
  carry no standard error.
"""

import warnings
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats

from econometrica.econ._common import build_manifest, coerce_params, require_columns
from econometrica.econ.multivariate._shared import COLUMNS_FIELD_DOC, prepare_frame
from econometrica.econ.registry import Gate, get_registry
from econometrica.econ.types import Diagnostic, Estimate, ResultSet, Series, Table

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "statsmodels")

_Det = Literal["none", "const", "trend"]

_DET_FIELD_DOC = (
    "Deterministic terms: 'none', 'const' (intercept) or 'trend' (intercept"
    " plus linear trend), applied outside the cointegration relation."
)
_DET_ORDER = {"none": -1, "const": 0, "trend": 1}
_DET_VECM = {"none": "n", "const": "co", "trend": "colo"}
_DET_NAMES = {"none": [], "const": ["const"], "trend": ["const", "trend"]}

_K_AR_DIFF_DOC = "Number of lagged differences in the VECM representation."


class JohansenParams(BaseModel):
    """Options for the Johansen cointegration rank test."""

    columns: list[str] = Field(default_factory=list, description=COLUMNS_FIELD_DOC)
    det: _Det = Field(default="const", description=_DET_FIELD_DOC)
    k_ar_diff: int = Field(default=1, ge=0, description=_K_AR_DIFF_DOC)
    min_obs: int = Field(
        default=50, ge=30, description="Minimum complete observations required."
    )


class EngleGrangerParams(BaseModel):
    """Options for the Engle-Granger two-step cointegration test."""

    y: str = Field(default="y", description="Left-hand-side column of the cointegrating OLS.")
    x: list[str] = Field(
        default_factory=lambda: ["x"],
        min_length=1,
        description="Right-hand-side column(s) of the cointegrating OLS.",
    )
    trend: Literal["n", "c", "ct", "ctt"] = Field(
        default="c",
        description="Deterministic terms in the cointegrating regression: 'n'"
        " none, 'c' constant, 'ct' constant + trend, 'ctt' + quadratic trend.",
    )
    min_obs: int = Field(
        default=30, ge=20, description="Minimum complete observations required."
    )


class VecmParams(BaseModel):
    """Options for the vector error correction model."""

    columns: list[str] = Field(default_factory=list, description=COLUMNS_FIELD_DOC)
    rank: int | None = Field(
        default=None,
        ge=1,
        description="Cointegration rank. None determines it by refitting the"
        " Johansen trace test at 95% on the same data (same det/k_ar_diff)"
        " and raises if that finds rank 0 — a VECM on non-cointegrated"
        " series is misspecified; pass rank explicitly to override.",
    )
    det: _Det = Field(default="const", description=_DET_FIELD_DOC)
    k_ar_diff: int = Field(default=1, ge=0, description=_K_AR_DIFF_DOC)
    min_obs: int = Field(
        default=50, ge=30, description="Minimum complete observations required."
    )


def _fit_johansen(values: np.ndarray, det_order: int, k_ar_diff: int) -> Any:
    from statsmodels.tsa.vector_ar.vecm import coint_johansen

    with warnings.catch_warnings():
        # Under this numpy/statsmodels combination coint_johansen casts the
        # eigendecomposition's numerically-complex results to real
        # (vecm.py lines 731-732) and numpy emits ComplexWarning. The
        # imaginary parts are eigensolver noise (~1e-16) and the statistics
        # are correct, so exactly this call suppresses exactly that warning.
        warnings.simplefilter("ignore", np.exceptions.ComplexWarning)
        return coint_johansen(values, det_order, k_ar_diff)


def _determine_rank(trace_stats: np.ndarray, cv95: np.ndarray) -> int:
    """Sequential trace procedure: first r whose H0 (rank <= r) survives."""
    for r in range(len(trace_stats)):
        if trace_stats[r] < cv95[r]:
            return r
    return len(trace_stats)


def _norm_ci(value: float, se: float) -> tuple[float, float]:
    z95 = float(stats.norm.ppf(0.975))
    return value - z95 * se, value + z95 * se


@get_registry().register(
    name="johansen",
    version=_VERSION,
    family="multivariate",
    summary="Johansen cointegration test: trace and max-eigenvalue statistics"
    " with 90/95/99% critical values per rank hypothesis, the rank determined"
    " by the sequential trace procedure at 95%, and the normalized"
    " cointegrating vector(s) for the determined rank.",
    params_model=JohansenParams,
    preconditions=(
        "every selected column holds one I(1) level series (prices, not returns)",
        "rows with a NaN in any selected column are dropped",
    ),
)
def johansen(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, JohansenParams)
    frame = prepare_frame(data, p.columns, min_obs=p.min_obs, tool="johansen")
    columns = [str(c) for c in frame.columns]
    k = len(columns)

    fitted = _fit_johansen(frame.to_numpy(), _DET_ORDER[p.det], p.k_ar_diff)
    trace = np.real(np.asarray(fitted.lr1, dtype=complex)).astype(float)
    max_eig = np.real(np.asarray(fitted.lr2, dtype=complex)).astype(float)
    trace_cv = np.asarray(fitted.cvt, dtype=float)  # columns: 90/95/99
    max_eig_cv = np.asarray(fitted.cvm, dtype=float)
    evec = np.real(np.asarray(fitted.evec, dtype=complex)).astype(float)

    selected_rank = _determine_rank(trace, trace_cv[:, 1])

    diagnostics: list[Diagnostic] = []
    rows: list[list[Any]] = []
    for r in range(k):
        rows.append(
            [
                float(r),
                float(trace[r]),
                float(trace_cv[r, 0]),
                float(trace_cv[r, 1]),
                float(trace_cv[r, 2]),
                float(max_eig[r]),
                float(max_eig_cv[r, 0]),
                float(max_eig_cv[r, 1]),
                float(max_eig_cv[r, 2]),
            ]
        )
        for stat_name, stat, cv, alternative in (
            ("trace", trace[r], trace_cv[r], f"rank > {r}"),
            ("max_eig", max_eig[r], max_eig_cv[r], f"rank = {r + 1}"),
        ):
            diagnostics.append(
                Diagnostic(
                    name=f"johansen_{stat_name}_r{r}",
                    statistic=float(stat),
                    p_value=None,
                    critical_values={
                        "90%": float(cv[0]),
                        "95%": float(cv[1]),
                        "99%": float(cv[2]),
                    },
                    passed=bool(stat > cv[1]),
                    interpretation=f"H0: cointegration rank <= {r} against"
                    f" {alternative}. passed means H0 is REJECTED at 5% (the"
                    " statistic exceeds the 95% critical value) — evidence of"
                    " more cointegrating relations. No p-value: only tabulated"
                    " critical values exist.",
                )
            )

    tables = {
        "rank_tests": Table(
            columns=[
                "rank_le",
                "trace_stat",
                "trace_cv_90",
                "trace_cv_95",
                "trace_cv_99",
                "max_eig_stat",
                "max_eig_cv_90",
                "max_eig_cv_95",
                "max_eig_cv_99",
            ],
            rows=rows,
        )
    }
    if selected_rank >= 1:
        vec_rows: list[list[Any]] = []
        for i, column in enumerate(columns):
            row: list[Any] = [column]
            for j in range(selected_rank):
                lead = evec[0, j]
                # Normalize each relation on the first variable when possible;
                # a ~0 leading loading is left unnormalized rather than blown up.
                scale = lead if abs(lead) > 1e-12 else 1.0
                row.append(float(evec[i, j] / scale))
            vec_rows.append(row)
        tables["cointegrating_vectors"] = Table(
            columns=["variable"] + [f"relation_{j + 1}" for j in range(selected_rank)],
            rows=vec_rows,
        )

    return ResultSet(
        tool="johansen",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars={
            "selected_rank": float(selected_rank),
            "n_variables": float(k),
            "nobs": float(len(frame)),
        },
        tables=tables,
        manifest=build_manifest(
            data, p, tool="johansen", version=_VERSION, libraries=_LIBRARIES
        ),
    )


@get_registry().register(
    name="engle_granger",
    version=_VERSION,
    family="multivariate",
    summary="Engle-Granger two-step cointegration test: cointegrating OLS of y"
    " on x, then an ADF-type test on the residuals with Engle-Granger critical"
    " values. Reports the cointegrating coefficients (OLS standard errors on"
    " I(1) regressors are descriptive only — use vecm for inference) and the"
    " equilibrium spread series.",
    params_model=EngleGrangerParams,
    preconditions=(
        "y and x columns hold I(1) level series (prices, not returns)",
        "rows with a NaN in any used column are dropped",
    ),
)
def engle_granger(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    import statsmodels.api as sm
    from statsmodels.tsa.stattools import coint

    p = coerce_params(params, EngleGrangerParams)
    columns = [p.y, *p.x]
    require_columns(data, columns, tool="engle_granger")
    frame = data[columns].dropna().astype(float)
    if len(frame) < p.min_obs:
        raise ValueError(
            f"engle_granger: needs at least {p.min_obs} complete observations,"
            f" got {len(frame)}; supply more data or lower min_obs"
        )

    y = frame[p.y].to_numpy()
    x = frame[p.x].to_numpy()

    # Step 1: cointegrating OLS with the same deterministic terms as the test.
    n = len(frame)
    det_parts: list[np.ndarray] = []
    det_names: list[str] = []
    if p.trend in ("c", "ct", "ctt"):
        det_parts.append(np.ones(n))
        det_names.append("const")
    if p.trend in ("ct", "ctt"):
        det_parts.append(np.arange(1.0, n + 1.0))
        det_names.append("trend")
    if p.trend == "ctt":
        det_parts.append(np.arange(1.0, n + 1.0) ** 2)
        det_names.append("trend_sq")
    design = np.column_stack([*det_parts, x]) if det_parts else x
    fit = sm.OLS(y, design).fit()
    names = det_names + list(p.x)
    ci = np.asarray(fit.conf_int())
    estimates = [
        Estimate(
            name=names[i],
            value=float(fit.params[i]),
            std_error=float(fit.bse[i]),
            t_stat=float(fit.tvalues[i]),
            p_value=float(fit.pvalues[i]),
            ci_low=float(ci[i, 0]),
            ci_high=float(ci[i, 1]),
        )
        for i in range(len(names))
    ]

    # Step 2: residual unit root test with Engle-Granger critical values.
    stat, p_value, crit = coint(y, x, trend=p.trend)
    diagnostic = Diagnostic(
        name="engle_granger",
        statistic=float(stat),
        p_value=float(p_value),
        critical_values={
            "1%": float(crit[0]),
            "5%": float(crit[1]),
            "10%": float(crit[2]),
        },
        passed=bool(p_value < 0.05),
        interpretation="H0: no cointegration (the residuals of the"
        " cointegrating OLS keep a unit root). passed means H0 is REJECTED at"
        " 5% — the series share a stationary equilibrium relation. Failing to"
        " reject on I(0) inputs is meaningless; feed level series.",
    )

    spread = pd.Series(np.asarray(fit.resid, dtype=float), index=frame.index)
    return ResultSet(
        tool="engle_granger",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=[diagnostic],
        scalars={"nobs": float(n)},
        series={"spread": Series(name="spread", x=_iso(frame.index), y=list(spread))},
        manifest=build_manifest(
            data, p, tool="engle_granger", version=_VERSION, libraries=_LIBRARIES
        ),
    )


def _iso(index: pd.Index) -> list[str]:
    from econometrica.econ._common import iso_index

    return iso_index(index)


@get_registry().register(
    name="vecm",
    version=_VERSION,
    family="multivariate",
    summary="Vector error correction model: adjustment coefficients (alpha),"
    " the normalized cointegrating vector(s) (beta, leading block fixed to the"
    " identity), short-run dynamics, and the error-correction term series."
    " rank=None determines the rank via the Johansen trace test at 95% on the"
    " same data.",
    params_model=VecmParams,
    preconditions=(
        "every selected column holds one I(1) level series (prices, not returns)",
        "the series are cointegrated (rank >= 1); the tool raises when the"
        " determined rank is 0",
    ),
    gates=(
        # The mirror image of the VAR gate, and the reason `expect` exists:
        # a VECM presumes I(1) levels sharing a long-run relation. On
        # stationary series there is no error to correct.
        Gate(
            check="stationarity",
            expect=False,
            because=(
                "a VECM presumes non-stationary levels bound by a"
                " cointegrating relation; on stationary series there is no"
                " error-correction term to estimate — fit a VAR instead"
            ),
        ),
    ),
)
def vecm(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from statsmodels.tsa.vector_ar.vecm import VECM

    p = coerce_params(params, VecmParams)
    frame = prepare_frame(data, p.columns, min_obs=p.min_obs, tool="vecm")
    columns = [str(c) for c in frame.columns]
    k = len(columns)
    values = frame.to_numpy()

    rank = p.rank
    if rank is None:
        fitted_johansen = _fit_johansen(values, _DET_ORDER[p.det], p.k_ar_diff)
        trace = np.real(np.asarray(fitted_johansen.lr1, dtype=complex)).astype(float)
        cv95 = np.asarray(fitted_johansen.cvt, dtype=float)[:, 1]
        rank = _determine_rank(trace, cv95)
        if rank == 0:
            raise ValueError(
                "vecm: the Johansen trace test determined rank 0 — no"
                f" cointegration among {columns} at 95% — and a VECM on"
                " non-cointegrated series is misspecified; difference the data"
                " and fit var_model instead, or pass rank explicitly to override"
            )
    if rank >= k:
        raise ValueError(
            f"vecm: rank {rank} with {k} variables means the system is"
            " stationary in levels; fit var_model on the levels instead"
        )

    with warnings.catch_warnings():
        # statsmodels' VECM assembles its covariance blocks with np.matrix,
        # which numpy flags with PendingDeprecationWarning. Library-internal
        # and harmless; the standard errors are LAZY properties, so the whole
        # extraction happens inside the guard, not just .fit().
        warnings.simplefilter("ignore", PendingDeprecationWarning)
        fit = VECM(
            values, k_ar_diff=p.k_ar_diff, coint_rank=rank, deterministic=_DET_VECM[p.det]
        ).fit()
        alpha = np.asarray(fit.alpha, dtype=float)
        alpha_se = np.asarray(fit.stderr_alpha, dtype=float)
        alpha_t = np.asarray(fit.tvalues_alpha, dtype=float)
        alpha_p = np.asarray(fit.pvalues_alpha, dtype=float)
        beta = np.real(np.asarray(fit.beta, dtype=complex)).astype(float)
        beta_se = np.asarray(fit.stderr_beta, dtype=float)
        beta_t = np.asarray(fit.tvalues_beta, dtype=float)
        beta_p = np.asarray(fit.pvalues_beta, dtype=float)
        gamma = np.asarray(fit.gamma, dtype=float)
        gamma_se = np.asarray(fit.stderr_gamma, dtype=float)
        gamma_t = np.asarray(fit.tvalues_gamma, dtype=float)
        gamma_p = np.asarray(fit.pvalues_gamma, dtype=float)
        det_arrays = (
            (
                np.asarray(fit.det_coef, dtype=float),
                np.asarray(fit.stderr_det_coef, dtype=float),
                np.asarray(fit.tvalues_det_coef, dtype=float),
                np.asarray(fit.pvalues_det_coef, dtype=float),
            )
            if _DET_NAMES[p.det]
            else None
        )
        nobs = float(fit.nobs)
        # statsmodels computes the VECM log-likelihood through a complex
        # eigendecomposition; the imaginary part is numerical noise.
        llf = float(np.real(fit.llf))

    estimates: list[Estimate] = []
    for j in range(rank):
        for i, column in enumerate(columns):
            lo, hi = _norm_ci(float(alpha[i, j]), float(alpha_se[i, j]))
            estimates.append(
                Estimate(
                    name=f"alpha.r{j + 1}.{column}",
                    value=float(alpha[i, j]),
                    std_error=float(alpha_se[i, j]),
                    t_stat=float(alpha_t[i, j]),
                    p_value=float(alpha_p[i, j]),
                    ci_low=lo,
                    ci_high=hi,
                )
            )
    for j in range(rank):
        for i, column in enumerate(columns):
            se = float(beta_se[i, j])
            if i < rank:
                # Leading block fixed to the identity by normalization:
                # a constant, not an estimate — no standard error.
                estimates.append(Estimate(name=f"beta.r{j + 1}.{column}", value=float(beta[i, j])))
            else:
                lo, hi = _norm_ci(float(beta[i, j]), se)
                estimates.append(
                    Estimate(
                        name=f"beta.r{j + 1}.{column}",
                        value=float(beta[i, j]),
                        std_error=se,
                        t_stat=float(beta_t[i, j]),
                        p_value=float(beta_p[i, j]),
                        ci_low=lo,
                        ci_high=hi,
                    )
                )

    lag_names = [
        f"L{lag}.d.{column}" for lag in range(1, p.k_ar_diff + 1) for column in columns
    ]
    rows: list[list[Any]] = []
    for i, equation in enumerate(columns):
        for g, regressor in enumerate(lag_names):
            rows.append(
                [
                    equation,
                    regressor,
                    float(gamma[i, g]),
                    float(gamma_se[i, g]),
                    float(gamma_t[i, g]),
                    float(gamma_p[i, g]),
                ]
            )
    det_names = _DET_NAMES[p.det]
    if det_names and det_arrays is not None:
        det_coef, det_se, det_t, det_p = det_arrays
        for i, equation in enumerate(columns):
            for d, regressor in enumerate(det_names):
                rows.append(
                    [
                        equation,
                        regressor,
                        float(det_coef[i, d]),
                        float(det_se[i, d]),
                        float(det_t[i, d]),
                        float(det_p[i, d]),
                    ]
                )

    ect_series = {
        f"ect_r{j + 1}": Series(
            name=f"ect_r{j + 1}",
            x=_iso(frame.index),
            y=[float(v) for v in values @ beta[:, j]],
        )
        for j in range(rank)
    }

    return ResultSet(
        tool="vecm",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        scalars={
            "rank": float(rank),
            "k_ar_diff": float(p.k_ar_diff),
            "nobs": nobs,
            "llf": llf,
        },
        tables={
            "short_run": Table(
                columns=["equation", "regressor", "coef", "std_error", "t_stat", "p_value"],
                rows=rows,
            )
        },
        series=ect_series,
        manifest=build_manifest(data, p, tool="vecm", version=_VERSION, libraries=_LIBRARIES),
    )
