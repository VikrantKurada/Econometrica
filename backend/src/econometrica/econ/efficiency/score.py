"""Composite weak-form efficiency score.

Aggregates six components of the efficiency family — ADF and KPSS on the
price levels, plus variance ratio, runs, Ljung-Box and Hurst on the price
path / its returns — into one deterministic 0-100 score with a categorical
verdict. Every mapping and threshold is a documented module constant or a
params field; nothing is data-dependent.

Sub-score design (each component maps to [0, 1], 1 = consistent with
weak-form efficiency):

- p-value components (adf, variance_ratio, runs, ljung_box): the null of each
  is consistent with a random walk, so evidence AGAINST the null is evidence
  of inefficiency. Sub-score = clip((p - P_FLOOR) / (P_CEIL - P_FLOOR), 0, 1):
  1 when p >= 0.10 (no evidence against randomness at conventional levels),
  0 when p <= 0.01 (strong rejection), linear between.
- kpss: the null is stationarity — the REVERSE — so its sub-score is one
  minus the same mapping (a random walk makes KPSS reject, which supports
  efficiency).
- variance_ratio: tested at several horizons; the component p-value is the
  Sidak-adjusted minimum across horizons, 1 - (1 - min_p)^m, which corrects
  for taking the best of m looks and is conservative under the strong
  positive dependence between VR statistics.
- hurst: no p-value exists; sub-score = clip(1 - |H - 0.5| / HURST_HALF_WIDTH,
  0, 1), full credit at the random-walk value 0.5, none once H is 0.2 away
  (strong persistence or anti-persistence).

Default weights halve the unit-root leg (adf, kpss at 0.5 versus 1.0 for the
return-predictability legs): ANY price path with stationary returns is I(1),
however predictable those returns are, so the unit-root leg mostly rules out
extreme price-level mean reversion and carries little information about
return predictability — the heart of weak-form efficiency.

BDS is deliberately excluded: its null is i.i.d., which volatility clustering
violates even when returns are unpredictable in mean — a GARCH series is
weak-form efficient in the sense scored here yet fails BDS. Run the ``bds``
tool separately to probe nonlinear structure.

The composite is scaled to 0-100 and mapped to a verdict at documented
thresholds; the summary diagnostic's tri-state ``passed`` mirrors it
(True = efficient, None = borderline, False = inefficient).
"""

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import build_manifest, coerce_params, require_columns
from econometrica.econ.efficiency.randomness import (
    HurstParams,
    LjungBoxParams,
    RunsTestParams,
    hurst,
    ljung_box,
    runs_test,
)
from econometrica.econ.efficiency.unit_root import AdfParams, KpssParams, adf, kpss
from econometrica.econ.efficiency.variance_ratio import VarianceRatioParams, variance_ratio
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet, Table

_VERSION = "1.0.0"
_LIBRARIES = ("arch", "numpy", "pandas", "scipy", "statsmodels")

#: p-value below which a rejection counts as total evidence (sub-score 0).
P_FLOOR = 0.01
#: p-value above which no evidence against the null is counted (sub-score 1).
P_CEIL = 0.10
#: |H - 0.5| at which the Hurst sub-score reaches 0 (0.5 scores 1).
HURST_HALF_WIDTH = 0.2
#: Composite score at or above which the verdict is "efficient".
EFFICIENT_THRESHOLD = 70.0
#: Composite score below which the verdict is "inefficient".
INEFFICIENT_THRESHOLD = 40.0

#: The randomness tools need the most data (R/S wants 256 return observations).
_MIN_LEVEL_OBS = 257


class WeakFormScoreParams(BaseModel):
    """Input binding, component options and weights for the composite score."""

    column: str = Field(
        default="price", description="Column holding the price (level) path to score."
    )
    return_method: Literal["diff", "log_diff"] = Field(
        default="log_diff",
        description="How returns are built from the levels: 'log_diff' for real"
        " price data (requires strictly positive prices), 'diff' for synthetic"
        " or already-log level paths.",
    )
    horizons: list[int] = Field(
        default=[2, 4, 8, 16],
        description="Variance ratio horizons (each >= 2); the component p-value"
        " is the Sidak-adjusted minimum across them.",
    )
    lb_lag: int = Field(default=10, ge=1, description="Ljung-Box lag for the autocorrelation leg.")
    weight_adf: float = Field(
        default=0.5,
        ge=0.0,
        description="Weight of the ADF leg (halved by default: any cumulated"
        " stationary-return path is I(1), so it discriminates weakly).",
    )
    weight_kpss: float = Field(
        default=0.5, ge=0.0, description="Weight of the KPSS leg (halved, same reason as ADF)."
    )
    weight_variance_ratio: float = Field(
        default=1.0, ge=0.0, description="Weight of the variance ratio leg."
    )
    weight_runs: float = Field(default=1.0, ge=0.0, description="Weight of the runs test leg.")
    weight_ljung_box: float = Field(
        default=1.0, ge=0.0, description="Weight of the Ljung-Box leg."
    )
    weight_hurst: float = Field(
        default=1.0, ge=0.0, description="Weight of the Hurst exponent leg."
    )


def _p_subscore(p_value: float) -> float:
    """Map a p-value to [0, 1]: 1 at ``p >= P_CEIL``, 0 at ``p <= P_FLOOR``."""
    return min(1.0, max(0.0, (p_value - P_FLOOR) / (P_CEIL - P_FLOOR)))


def _hurst_subscore(estimate: float) -> float:
    """Full credit at H = 0.5, falling linearly to 0 at |H - 0.5| >= HURST_HALF_WIDTH."""
    return min(1.0, max(0.0, 1.0 - abs(estimate - 0.5) / HURST_HALF_WIDTH))


@get_registry().register(
    name="weak_form_efficiency_score",
    version=_VERSION,
    family="efficiency",
    summary="Composite 0-100 weak-form efficiency score over a price series:"
    " unit root, variance ratio, runs, Ljung-Box and Hurst legs combined by"
    " documented weights into an efficient/borderline/inefficient verdict.",
    params_model=WeakFormScoreParams,
    preconditions=(
        "the selected column holds one regularly observed price (level) series",
        "at least ~260 observations (the R/S leg needs 256 returns)",
    ),
)
def weak_form_efficiency_score(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, WeakFormScoreParams)
    tool = "weak_form_efficiency_score"
    require_columns(data, [p.column], tool=tool)
    if any(h < 2 for h in p.horizons) or not p.horizons:
        raise ValueError(f"{tool}: every variance ratio horizon must be >= 2, got {p.horizons}")

    n_levels = int(data[p.column].notna().sum())
    if n_levels < _MIN_LEVEL_OBS:
        raise ValueError(
            f"{tool}: needs at least {_MIN_LEVEL_OBS} observations (the R/S leg"
            f" requires 256 returns), got {n_levels}"
        )

    weights = {
        "adf": p.weight_adf,
        "kpss": p.weight_kpss,
        "variance_ratio": p.weight_variance_ratio,
        "runs": p.weight_runs,
        "ljung_box": p.weight_ljung_box,
        "hurst": p.weight_hurst,
    }
    total_weight = sum(weights.values())
    if total_weight <= 0.0:
        raise ValueError(f"{tool}: at least one component weight must be positive")

    # Levels for the unit-root and variance ratio legs; returns for the rest.
    level_transform: Literal["none", "log"] = "none" if p.return_method == "diff" else "log"
    return_transform = p.return_method

    adf_diag = adf(
        data, AdfParams(column=p.column, transform=level_transform)
    ).diagnostics[0]
    kpss_diag = kpss(
        data, KpssParams(column=p.column, transform=level_transform)
    ).diagnostics[0]

    vr_result = variance_ratio(
        data,
        VarianceRatioParams(column=p.column, transform=level_transform, horizons=p.horizons),
    )
    vr_best = min(vr_result.diagnostics, key=lambda d: d.p_value or 1.0)
    vr_min_p = vr_best.p_value if vr_best.p_value is not None else 1.0
    vr_p = 1.0 - (1.0 - vr_min_p) ** len(sorted(set(p.horizons)))

    runs_diag = runs_test(
        data, RunsTestParams(column=p.column, transform=return_transform)
    ).diagnostics[0]
    lb_diag = ljung_box(
        data, LjungBoxParams(column=p.column, transform=return_transform, lags=[p.lb_lag])
    ).diagnostics[0]
    hurst_estimate = hurst(
        data, HurstParams(column=p.column, transform=return_transform)
    ).scalars["hurst"]

    # (statistic, p-value, sub-score) per component; p-value None for hurst.
    components: dict[str, tuple[float, float | None, float]] = {
        "adf": (adf_diag.statistic, adf_diag.p_value, _p_subscore(adf_diag.p_value or 0.0)),
        "kpss": (
            kpss_diag.statistic,
            kpss_diag.p_value,
            1.0 - _p_subscore(kpss_diag.p_value or 0.0),  # reversed null
        ),
        "variance_ratio": (vr_best.statistic, vr_p, _p_subscore(vr_p)),
        "runs": (runs_diag.statistic, runs_diag.p_value, _p_subscore(runs_diag.p_value or 0.0)),
        "ljung_box": (lb_diag.statistic, lb_diag.p_value, _p_subscore(lb_diag.p_value or 0.0)),
        "hurst": (hurst_estimate, None, _hurst_subscore(hurst_estimate)),
    }

    rows: list[list[object]] = []
    diagnostics: list[Diagnostic] = []
    score = 0.0
    for name, (statistic, p_value, sub_score) in components.items():
        weight = weights[name]
        contribution = 100.0 * weight * sub_score / total_weight
        score += contribution
        rows.append([name, float(weight), float(sub_score), statistic, p_value, contribution])
        diagnostics.append(
            Diagnostic(
                name=name,
                statistic=statistic,
                p_value=p_value,
                passed=bool(sub_score >= 0.5),
                interpretation=f"Component sub-score {sub_score:.3f} (weight {weight});"
                " passed means this component finds no material evidence against"
                " weak-form efficiency (sub-score >= 0.5).",
            )
        )

    if score >= EFFICIENT_THRESHOLD:
        verdict, passed = "efficient", True
    elif score < INEFFICIENT_THRESHOLD:
        verdict, passed = "inefficient", False
    else:
        verdict, passed = "borderline", None
    summary = Diagnostic(
        name="weak_form_efficiency",
        statistic=float(score),
        passed=passed,
        interpretation=f"verdict: {verdict}. Composite score {score:.1f}/100"
        f" (efficient >= {EFFICIENT_THRESHOLD:.0f}, inefficient <"
        f" {INEFFICIENT_THRESHOLD:.0f}); weighted mean of six documented"
        " component sub-scores. passed is True/None/False for"
        " efficient/borderline/inefficient.",
    )

    return ResultSet(
        tool=tool,
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=[summary, *diagnostics],
        scalars={
            "score": float(score),
            "nobs": float(n_levels),
            "n_components": float(len(components)),
        },
        tables={
            "components": Table(
                columns=[
                    "component",
                    "weight",
                    "sub_score",
                    "statistic",
                    "p_value",
                    "contribution",
                ],
                rows=rows,
            )
        },
        manifest=build_manifest(data, p, tool=tool, version=_VERSION, libraries=_LIBRARIES),
    )
