"""Deterministic assumption checks over a fitted model's residuals.

This is NOT a registered tool. The orchestrator calls it directly, and the
result is handed to the Validator agent as *facts* — the whole point is that an
LLM never has to infer whether residuals are heteroskedastic or autocorrelated.
Every check is deterministic: identical input yields identical output.

Three ``passed`` semantics coexist across the codebase, and this module uses
the first two:

* **fail-to-reject-is-good** — normality, homoskedasticity, no-autocorrelation,
  no-ARCH, parameter stability. ``passed=True`` means the assumption survived.
* **structural threshold, no p-value** — Durbin-Watson, VIF, structural break.
  ``passed`` comes from a documented cutoff, and ``p_value`` is ``None``.

``passed=None`` always means "not judged" — never "failed".
"""

from typing import Literal, get_args

import numpy as np
import pandas as pd

from econometrica.econ.types import Diagnostic

Check = Literal[
    "jarque_bera",
    "breusch_pagan",
    "white",
    "durbin_watson",
    "ljung_box",
    "arch_lm",
    "vif",
    "cusum",
    "structural_break",
]

ALL_CHECKS: tuple[Check, ...] = get_args(Check)

#: Checks that cannot run without a design matrix.
_NEEDS_EXOG: frozenset[str] = frozenset({"breusch_pagan", "white", "vif"})

#: Minimum residuals before any statistic is meaningful.
MIN_OBS = 20

#: Durbin-Watson band treated as "no material first-order autocorrelation".
#: The statistic runs 0..4 and centres on 2; this is the conventional rule of
#: thumb, and it is the single Durbin-Watson convention in this codebase —
#: ``econ._common.ols_residual_diagnostics`` delegates here rather than
#: carrying its own copy.
DW_LOW, DW_HIGH = 1.5, 2.5

#: Conventional variance-inflation cutoff for "material multicollinearity".
VIF_THRESHOLD = 10.0

#: A detected break counts as material when the mean shift across it exceeds
#: this many residual standard deviations. Below it, `ruptures` will still
#: return a location — it always does — but the shift is noise.
BREAK_EFFECT_THRESHOLD = 0.5


def default_lags(nobs: int) -> int:
    """Deterministic lag rule for the autocorrelation and ARCH checks.

    Fixed rather than data-dependent so two runs on the same residuals are
    bit-identical, which library defaults no longer guarantee.
    """
    return max(1, min(10, nobs // 5))


def run_diagnostics(
    resid: pd.Series,
    exog: pd.DataFrame | None = None,
    *,
    checks: list[str] | None = None,
    alpha: float = 0.05,
    lags: int | None = None,
) -> list[Diagnostic]:
    """Run the assumption battery over ``resid``.

    ``checks=None`` runs every check that is *applicable*: the three requiring
    a design matrix are skipped silently when ``exog`` is absent. Naming one of
    them explicitly without ``exog`` is a caller error and raises.
    """
    resid = resid.dropna().astype(float)
    if len(resid) < MIN_OBS:
        raise ValueError(
            f"diagnostics: needs at least {MIN_OBS} residuals, got {len(resid)}"
        )

    selected: list[str]
    if checks is None:
        selected = [c for c in ALL_CHECKS if _applicable(c, exog)]
    else:
        unknown = [c for c in checks if c not in ALL_CHECKS]
        if unknown:
            raise ValueError(
                f"diagnostics: unknown diagnostic check(s) {unknown}; "
                f"available checks are {list(ALL_CHECKS)}"
            )
        missing_exog = [c for c in checks if c in _NEEDS_EXOG and exog is None]
        if missing_exog:
            raise ValueError(
                f"diagnostics: {', '.join(missing_exog)} requires an exog design "
                "matrix; supply one or drop the check"
            )
        selected = list(checks)

    nlags = lags if lags is not None else default_lags(len(resid))
    values = resid.to_numpy()

    out: list[Diagnostic] = []
    for check in selected:
        if check == "jarque_bera":
            out.append(jarque_bera_check(values, alpha))
        elif check == "breusch_pagan":
            out.append(_breusch_pagan(values, _design(exog, resid), alpha))
        elif check == "white":
            out.append(_white(values, _design(exog, resid), alpha))
        elif check == "durbin_watson":
            out.append(durbin_watson_check(values))
        elif check == "ljung_box":
            out.append(_ljung_box(values, nlags, alpha))
        elif check == "arch_lm":
            out.append(_arch_lm(values, nlags, alpha))
        elif check == "vif":
            out.extend(_vif(_require_frame(exog)))
        elif check == "cusum":
            out.append(_cusum(values, alpha))
        elif check == "structural_break":
            out.append(_structural_break(values))
    return out


# --- individual checks ------------------------------------------------------


def jarque_bera_check(resid: np.ndarray, alpha: float = 0.05) -> Diagnostic:
    from statsmodels.stats.stattools import jarque_bera

    stat, p_value, _, _ = jarque_bera(resid)
    if not (np.isfinite(stat) and np.isfinite(p_value)):
        return _undefined("jarque_bera", "Residual normality")
    return Diagnostic(
        name="jarque_bera",
        statistic=float(stat),
        p_value=float(p_value),
        passed=bool(p_value >= alpha),
        interpretation=(
            "H0: residuals are normally distributed. passed means normality is "
            "not rejected at the chosen alpha."
        ),
    )


def _breusch_pagan(resid: np.ndarray, exog: np.ndarray, alpha: float) -> Diagnostic:
    from statsmodels.stats.diagnostic import het_breuschpagan

    stat, p_value, _, _ = het_breuschpagan(resid, exog)
    return Diagnostic(
        name="breusch_pagan",
        statistic=float(stat),
        p_value=float(p_value),
        passed=bool(p_value >= alpha),
        interpretation=(
            "H0: residual variance is constant in the regressors. passed means "
            "homoskedasticity is not rejected; failing suggests robust standard "
            "errors."
        ),
    )


def _white(resid: np.ndarray, exog: np.ndarray, alpha: float) -> Diagnostic:
    from statsmodels.stats.diagnostic import het_white

    stat, p_value, _, _ = het_white(resid, exog)
    return Diagnostic(
        name="white",
        statistic=float(stat),
        p_value=float(p_value),
        passed=bool(p_value >= alpha),
        interpretation=(
            "H0: residual variance is constant, testing squares and cross "
            "products of the regressors. passed means homoskedasticity is not "
            "rejected against this wider alternative than Breusch-Pagan."
        ),
    )


def durbin_watson_check(resid: np.ndarray) -> Diagnostic:
    from statsmodels.stats.stattools import durbin_watson

    stat = float(durbin_watson(resid))
    if not np.isfinite(stat):
        return _undefined("durbin_watson", "First-order residual autocorrelation")
    return Diagnostic(
        name="durbin_watson",
        statistic=stat,
        critical_values={"lower": DW_LOW, "upper": DW_HIGH},
        passed=bool(DW_LOW <= stat <= DW_HIGH),
        interpretation=(
            f"First-order residual autocorrelation; 2 means none, below {DW_LOW} "
            f"positive and above {DW_HIGH} negative. No p-value: passed comes "
            "from the conventional band, not a test."
        ),
    )


def _ljung_box(resid: np.ndarray, nlags: int, alpha: float) -> Diagnostic:
    from statsmodels.stats.diagnostic import acorr_ljungbox

    frame = acorr_ljungbox(resid, lags=[nlags], return_df=True)
    stat = float(frame["lb_stat"].iloc[0])
    p_value = float(frame["lb_pvalue"].iloc[0])
    return Diagnostic(
        name="ljung_box",
        statistic=stat,
        p_value=p_value,
        critical_values={"lags": float(nlags)},
        passed=bool(p_value >= alpha),
        interpretation=(
            f"H0: no autocorrelation up to lag {nlags}. passed means the "
            "residuals are indistinguishable from serially uncorrelated."
        ),
    )


def _arch_lm(resid: np.ndarray, nlags: int, alpha: float) -> Diagnostic:
    from statsmodels.stats.diagnostic import het_arch

    stat, p_value, _, _ = het_arch(resid, nlags=nlags)
    return Diagnostic(
        name="arch_lm",
        statistic=float(stat),
        p_value=float(p_value),
        critical_values={"lags": float(nlags)},
        passed=bool(p_value >= alpha),
        interpretation=(
            f"H0: no ARCH effects up to lag {nlags}. passed means no volatility "
            "clustering remains; failing argues for a GARCH-family model."
        ),
    )


def _vif(exog: pd.DataFrame) -> list[Diagnostic]:
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    non_constant = [c for c in exog.columns if exog[c].nunique() > 1]
    if len(non_constant) < 2:
        raise ValueError(
            "diagnostics: vif needs at least two non-constant regressors, got "
            f"{len(non_constant)} in {list(exog.columns)}"
        )

    design = exog.to_numpy(dtype=float)
    positions = {c: i for i, c in enumerate(exog.columns)}
    out: list[Diagnostic] = []
    for column in non_constant:
        value = float(variance_inflation_factor(design, positions[column]))
        finite = bool(np.isfinite(value))
        out.append(
            Diagnostic(
                name=f"vif_{column}",
                statistic=value if finite else 0.0,
                critical_values={"threshold": VIF_THRESHOLD},
                passed=bool(value < VIF_THRESHOLD) if finite else None,
                interpretation=(
                    f"Variance inflation for {column!r}; passed means it is below "
                    f"{VIF_THRESHOLD:g}, so multicollinearity is not inflating this "
                    "coefficient's standard error. No p-value: this is a threshold, "
                    "not a test."
                ),
            )
        )
    return out


def _cusum(resid: np.ndarray, alpha: float) -> Diagnostic:
    """Parameter stability via the CUSUM of residuals.

    The residuals are demeaned first. ``breaks_cusumolsresid`` assumes genuine
    OLS residuals from a model with an intercept, which sum to exactly zero; a
    series with any residual mean drift makes its cumulative sum grow linearly
    and the statistic then measures that mean rather than instability. Since
    the orchestrator may hand this engine any residual series, demeaning is the
    difference between a stability test and a test of whether the mean is zero.
    A genuine mid-sample break survives demeaning — it shifts the level, not
    the arc.
    """
    from statsmodels.stats.diagnostic import breaks_cusumolsresid

    stat, p_value, crit = breaks_cusumolsresid(resid - float(np.mean(resid)))
    return Diagnostic(
        name="cusum",
        statistic=float(stat),
        p_value=float(p_value),
        critical_values={str(level): float(value) for level, value in crit},
        passed=bool(p_value >= alpha),
        interpretation=(
            "H0: the model's parameters are stable through the sample. passed "
            "means the CUSUM of residuals stays within its bounds."
        ),
    )


def _structural_break(resid: np.ndarray) -> Diagnostic:
    """Locate the single most likely mean shift and judge whether it matters.

    `ruptures` always returns a location, so a location alone proves nothing.
    The judgement is the *effect size* — the mean shift across the break in
    residual standard deviations — against a documented threshold.

    ``statistic`` is the break position as a fraction of the sample (0..1), so
    it reads the same regardless of sample length. The raw index and the effect
    size go in ``critical_values``: that field is the only float-valued bag on
    a Diagnostic, and putting them there is deliberate — it makes them visible
    to ``ResultSet.all_numeric_values()``, so the Phase 4 grounding gate will
    let a narrator cite them.
    """
    import ruptures as rpt

    nobs = len(resid)
    scale = float(np.std(resid))
    if scale == 0.0:
        return _undefined("structural_break", "Structural break in the residual mean")

    breaks = rpt.Binseg(model="l2").fit(resid).predict(n_bkps=1)
    index = int(breaks[0])
    effect = abs(float(np.mean(resid[:index])) - float(np.mean(resid[index:]))) / scale
    material = effect >= BREAK_EFFECT_THRESHOLD

    return Diagnostic(
        name="structural_break",
        statistic=index / nobs,
        critical_values={
            "break_index": float(index),
            "effect_size": effect,
            "threshold": BREAK_EFFECT_THRESHOLD,
        },
        passed=not material,
        interpretation=(
            f"Most likely single mean shift sits at observation {index} of {nobs} "
            f"({index / nobs:.0%} through the sample), worth {effect:.2f} residual "
            f"standard deviations. passed means that shift is below the "
            f"{BREAK_EFFECT_THRESHOLD:g} threshold and is treated as noise. No "
            "p-value: this is a threshold on effect size, not a test."
        ),
    )


# --- helpers ----------------------------------------------------------------


def _applicable(check: str, exog: pd.DataFrame | None) -> bool:
    """Whether an auto-selected check can actually run on what we were given.

    Only consulted when the caller passed ``checks=None``. Naming a check
    explicitly always raises instead, so a caller who asked for something
    specific is never silently ignored.
    """
    if check in _NEEDS_EXOG and exog is None:
        return False
    if check == "vif" and exog is not None:
        return bool(sum(exog[c].nunique() > 1 for c in exog.columns) >= 2)
    return True


def _design(exog: pd.DataFrame | None, resid: pd.Series) -> np.ndarray:
    """Design matrix for the heteroskedasticity tests, constant guaranteed.

    Both statsmodels tests regress squared residuals on this matrix and need an
    intercept; adding one when absent avoids a misleading statistic.
    """
    frame = _require_frame(exog)
    if not any(frame[c].nunique() == 1 for c in frame.columns):
        frame = frame.assign(const=1.0)
    design: np.ndarray = frame.to_numpy(dtype=float)
    return design[: len(resid)]


def _require_frame(exog: pd.DataFrame | None) -> pd.DataFrame:
    if exog is None:  # pragma: no cover — guarded by run_diagnostics
        raise ValueError("diagnostics: this check requires an exog design matrix")
    return exog


def _undefined(name: str, what: str) -> Diagnostic:
    return Diagnostic(
        name=name,
        statistic=0.0,
        passed=None,
        interpretation=(
            f"{what} is undefined on degenerate residuals; passed is None, "
            "meaning not judged rather than failed."
        ),
    )
