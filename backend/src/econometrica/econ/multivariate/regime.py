"""Markov regime-switching model of a return series.

Wraps ``statsmodels`` MarkovRegression with a constant per regime and (by
default) a switching variance — the classic calm/turbulent decomposition of
financial returns.

Label-switching convention (documented once here, relied on by every output):
the likelihood is invariant to permuting regime labels, so the tool relabels
regimes in order of INCREASING VARIANCE (increasing mean when the variance is
not switching): ``regime_1`` is always the calmest. Estimates, smoothed
probabilities, the transition matrix and expected durations are all reported
under this ordering, which makes results comparable across runs and datasets.

On data with no true regimes (i.i.d. returns) the EM/quasi-Newton fit still
converges, typically to two near-identical regimes with arbitrary weights —
the correct null behaviour, reported as-is rather than raised.
"""

import warnings

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats

from econometrica.econ._common import (
    TRANSFORM_FIELD_DOC,
    Transform,
    build_manifest,
    coerce_params,
    prepare_series,
    series_from,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, Estimate, ResultSet, Table

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "statsmodels")


class MarkovSwitchingParams(BaseModel):
    """Options for the Markov regime-switching tool."""

    column: str = Field(
        default="return", description="Column holding per-period returns in DECIMAL units."
    )
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    k_regimes: int = Field(default=2, ge=2, le=4, description="Number of regimes.")
    switching_variance: bool = Field(
        default=True,
        description="True lets the variance differ per regime (volatility"
        " regimes); False switches the mean only.",
    )
    min_obs: int = Field(
        default=100,
        ge=60,
        description="Minimum observations after transforming; regime"
        " likelihoods need enough data per regime.",
    )


@get_registry().register(
    name="markov_switching",
    version=_VERSION,
    family="multivariate",
    summary="Fit a Markov regime-switching model (per-regime mean, switching"
    " variance by default) with smoothed regime probabilities, the transition"
    " matrix and expected regime durations. Regimes are relabeled by"
    " increasing variance so regime_1 is always the calmest.",
    params_model=MarkovSwitchingParams,
    preconditions=(
        "the selected column holds one regularly observed return series in decimal units",
        "at least ~100 observations; NaNs are dropped",
    ),
)
def markov_switching(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

    p = coerce_params(params, MarkovSwitchingParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="markov_switching"
    )
    k = p.k_regimes

    model = MarkovRegression(
        series.to_numpy(),
        k_regimes=k,
        trend="c",
        switching_variance=p.switching_variance,
    )
    # The EM starts and quasi-Newton refinement probe near-degenerate regime
    # allocations and numpy warns (log of ~0 probabilities). Finiteness of the
    # fit is checked explicitly below, and result properties are LAZY, so the
    # whole extraction happens inside the guard, not just .fit().
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            fit = model.fit()
        except Exception as exc:  # statsmodels raises plain errors on degenerate input
            raise ValueError(
                f"markov_switching: estimation failed ({exc}); check that column"
                f" {p.column!r} holds a return series with variation and enough"
                " observations per regime"
            ) from exc
        values = np.asarray(fit.params, dtype=float)
        bse = np.asarray(fit.bse, dtype=float)
        tvalues = np.asarray(fit.tvalues, dtype=float)
        pvalues = np.asarray(fit.pvalues, dtype=float)
        smoothed = np.asarray(fit.smoothed_marginal_probabilities, dtype=float)
        # regime_transition[to, from] in statsmodels; squeeze the time axis.
        transition = np.squeeze(np.asarray(fit.regime_transition, dtype=float), axis=2)
        durations = np.asarray(fit.expected_durations, dtype=float)
        llf = float(fit.llf)
        aic = float(fit.aic)
        bic = float(fit.bic)
        converged = bool(fit.mle_retvals.get("converged", False))

    if not np.all(np.isfinite(values)) or not np.isfinite(llf):
        raise ValueError(
            "markov_switching: the optimizer did not reach a usable fit; the"
            f" series in column {p.column!r} may be constant or too short for"
            f" {k} regimes"
        )

    names = list(model.param_names)
    by_name = {
        name: (float(values[i]), float(bse[i]), float(tvalues[i]), float(pvalues[i]))
        for i, name in enumerate(names)
    }

    means = np.array([by_name[f"const[{r}]"][0] for r in range(k)])
    if p.switching_variance:
        variances = np.array([by_name[f"sigma2[{r}]"][0] for r in range(k)])
        order = np.argsort(variances, kind="stable")
    else:
        order = np.argsort(means, kind="stable")

    z95 = float(stats.norm.ppf(0.975))

    def estimate(out_name: str, param_name: str) -> Estimate:
        value, se, t, p_value = by_name[param_name]
        return Estimate(
            name=out_name,
            value=value,
            std_error=se,
            t_stat=t if np.isfinite(t) else None,
            p_value=p_value if np.isfinite(p_value) else None,
            ci_low=value - z95 * se,
            ci_high=value + z95 * se,
        )

    estimates: list[Estimate] = []
    for out_r in range(k):
        orig = int(order[out_r])
        estimates.append(estimate(f"mu.regime_{out_r + 1}", f"const[{orig}]"))
    if p.switching_variance:
        for out_r in range(k):
            orig = int(order[out_r])
            estimates.append(estimate(f"sigma2.regime_{out_r + 1}", f"sigma2[{orig}]"))
    else:
        estimates.append(estimate("sigma2", "sigma2"))

    transition_rows: list[list[object]] = []
    for out_from in range(k):
        row: list[object] = [f"regime_{out_from + 1}"]
        for out_to in range(k):
            row.append(float(transition[int(order[out_to]), int(order[out_from])]))
        transition_rows.append(row)

    prob_series = {
        f"regime_{out_r + 1}_prob": series_from(
            f"regime_{out_r + 1}_prob",
            pd.Series(smoothed[:, int(order[out_r])], index=series.index),
        )
        for out_r in range(k)
    }

    scalars: dict[str, float] = {
        "llf": llf,
        "aic": aic,
        "bic": bic,
        "nobs": float(len(series)),
    }
    for out_r in range(k):
        scalars[f"expected_duration_regime_{out_r + 1}"] = float(durations[int(order[out_r])])

    diagnostics = [
        Diagnostic(
            name="convergence",
            statistic=1.0 if converged else 0.0,
            p_value=None,
            passed=converged,
            interpretation="Optimizer convergence flag (1 = converged). Not a"
            " hypothesis test. On data without true regimes the fit typically"
            " converges to near-identical regimes — the correct null"
            " behaviour, not an error.",
        )
    ]

    return ResultSet(
        tool="markov_switching",
        version=_VERSION,
        params=p.model_dump(),
        estimates=estimates,
        diagnostics=diagnostics,
        scalars=scalars,
        tables={
            "transition_matrix": Table(
                columns=["from_regime"] + [f"to_regime_{r + 1}" for r in range(k)],
                rows=transition_rows,
            )
        },
        series=prob_series,
        manifest=build_manifest(
            data, p, tool="markov_switching", version=_VERSION, libraries=_LIBRARIES
        ),
    )
