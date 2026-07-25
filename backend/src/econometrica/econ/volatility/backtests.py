"""VaR backtests: Kupiec unconditional coverage and Christoffersen
independence / conditional coverage.

Inputs follow the risk tools' POSITIVE-LOSS convention: the VaR forecast —
either an aligned per-period column (``var_column``) or a single static level
(``var_level``) — is the loss magnitude that should be exceeded with
probability 1 - confidence. A violation is a return STRICTLY below -VaR; a
return exactly at the boundary is not a violation.

``passed`` semantics (documented per diagnostic): H0 is that the VaR is
correctly calibrated, so ``passed=True`` means the test FAILS TO REJECT —
the desirable outcome for a good VaR model. The pair is complementary:
Kupiec sees only the violation COUNT, Christoffersen's independence LR sees
their TIMING, so a static VaR on volatility-clustered returns passes Kupiec
and fails Christoffersen.

Degenerate cases: the likelihood terms use the 0 * log(0) = 0 convention, so
zero violations (or all violations) keep Kupiec's LR finite — a VaR that is
never violated is rejected as too conservative, which is correct behaviour.
The independence LR, however, needs both states of the violation indicator
to occur; with no violations at all (or nothing but violations) it is not
judged: statistic 0.0, ``passed=None``, and an interpretation saying so.
With violations but none consecutive (n11 = 0) the LR is still well defined
under the same convention and is judged normally.
"""

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import build_manifest, coerce_params, require_columns
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "scipy")

_CONFIDENCE_DOC = (
    "VaR confidence level c: violations are expected with probability 1 - c"
    " (0.95 means a 5% expected violation rate)."
)


class BacktestParams(BaseModel):
    """Input binding shared by the two VaR backtests."""

    returns: str = Field(
        default="return", description="Column of per-period returns in DECIMAL units."
    )
    var_column: str | None = Field(
        default=None,
        description="Column of aligned per-period VaR forecasts as POSITIVE"
        " loss magnitudes. Exactly one of var_column and var_level is required.",
    )
    var_level: float | None = Field(
        default=None,
        description="Single static VaR level as a POSITIVE loss magnitude."
        " Exactly one of var_column and var_level is required.",
    )
    confidence: float = Field(default=0.95, gt=0.5, lt=1.0, description=_CONFIDENCE_DOC)
    min_obs: int = Field(
        default=250,
        ge=30,
        description="Minimum aligned observations; coverage tests on short"
        " samples have almost no power.",
    )


def _violation_indicator(data: pd.DataFrame, p: BacktestParams, *, tool: str) -> np.ndarray:
    """0/1 violation series under the strict ``r < -VaR`` definition."""
    if (p.var_column is None) == (p.var_level is None):
        raise ValueError(
            f"{tool}: exactly one of var_column and var_level must be supplied;"
            f" got var_column={p.var_column!r}, var_level={p.var_level!r}"
        )
    if p.var_column is not None:
        require_columns(data, [p.returns, p.var_column], tool=tool)
        aligned = data[[p.returns, p.var_column]].dropna().astype(float)
        returns = aligned[p.returns].to_numpy()
        var = aligned[p.var_column].to_numpy()
    else:
        require_columns(data, [p.returns], tool=tool)
        returns = data[p.returns].dropna().astype(float).to_numpy()
        var = np.full(len(returns), float(p.var_level))  # type: ignore[arg-type]
    if len(returns) < p.min_obs:
        raise ValueError(
            f"{tool}: needs at least {p.min_obs} aligned observations, got"
            f" {len(returns)}; supply more data or lower min_obs"
        )
    return np.asarray(returns < -var, dtype=np.int64)


def _xlogy(k: float, prob: float) -> float:
    """``k * log(prob)`` under the 0 * log(0) = 0 convention."""
    if k == 0.0:
        return 0.0
    return k * float(np.log(prob))


def _kupiec_lr(violations: int, n: int, expected_rate: float) -> float:
    """Kupiec's proportion-of-failures LR: binomial(x; n, p) vs binomial(x; n, x/n)."""
    observed_rate = violations / n
    ll_null = _xlogy(n - violations, 1.0 - expected_rate) + _xlogy(violations, expected_rate)
    ll_alt = _xlogy(n - violations, 1.0 - observed_rate) + _xlogy(violations, observed_rate)
    return -2.0 * (ll_null - ll_alt)


def _coverage_scalars(viol: np.ndarray, expected_rate: float) -> dict[str, float]:
    n = len(viol)
    x = int(viol.sum())
    return {
        "violations": float(x),
        "expected_violations": float(n * expected_rate),
        "violation_rate": float(x / n),
        "nobs": float(n),
    }


@get_registry().register(
    name="kupiec_test",
    version=_VERSION,
    family="volatility",
    summary="Kupiec unconditional coverage backtest: does the VaR (positive"
    " loss convention) get violated at the promised rate? passed=True means"
    " the coverage is not rejected — the VaR looks well calibrated.",
    params_model=BacktestParams,
    preconditions=(
        "the returns column holds decimal returns; VaR forecasts are positive loss magnitudes",
        "a violation is a return strictly below -VaR",
    ),
)
def kupiec_test(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from scipy import stats

    p = coerce_params(params, BacktestParams)
    viol = _violation_indicator(data, p, tool="kupiec_test")
    expected_rate = 1.0 - p.confidence

    scalars = _coverage_scalars(viol, expected_rate)
    lr = _kupiec_lr(int(scalars["violations"]), len(viol), expected_rate)
    p_value = float(stats.chi2.sf(lr, 1))

    diagnostic = Diagnostic(
        name="kupiec",
        statistic=lr,
        p_value=p_value,
        passed=bool(p_value >= 0.05),
        interpretation="H0: the violation rate equals the promised"
        f" {expected_rate:.4g}. passed means H0 is NOT rejected at 5% — the"
        " VaR's unconditional coverage looks right. Rejection means too many"
        " violations (VaR too small) or too few (too conservative); the"
        " violation count says which. Timing is invisible here — pair with"
        " christoffersen_test.",
    )
    return ResultSet(
        tool="kupiec_test",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=[diagnostic],
        scalars=scalars,
        manifest=build_manifest(
            data, p, tool="kupiec_test", version=_VERSION, libraries=_LIBRARIES
        ),
    )


@get_registry().register(
    name="christoffersen_test",
    version=_VERSION,
    family="volatility",
    summary="Christoffersen backtest: are VaR violations independent over time"
    " (no clustering), and jointly, is coverage conditionally correct?"
    " passed=True means not rejected. Catches the clustered-violation failure"
    " mode that Kupiec cannot see.",
    params_model=BacktestParams,
    preconditions=(
        "the returns column holds decimal returns; VaR forecasts are positive loss magnitudes",
        "a violation is a return strictly below -VaR",
    ),
)
def christoffersen_test(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from scipy import stats

    p = coerce_params(params, BacktestParams)
    viol = _violation_indicator(data, p, tool="christoffersen_test")
    expected_rate = 1.0 - p.confidence

    scalars = _coverage_scalars(viol, expected_rate)
    x = int(scalars["violations"])
    lr_uc = _kupiec_lr(x, len(viol), expected_rate)
    scalars["lr_unconditional"] = lr_uc

    previous, current = viol[:-1], viol[1:]
    n00 = float(np.sum((previous == 0) & (current == 0)))
    n01 = float(np.sum((previous == 0) & (current == 1)))
    n10 = float(np.sum((previous == 1) & (current == 0)))
    n11 = float(np.sum((previous == 1) & (current == 1)))
    scalars.update({"n00": n00, "n01": n01, "n10": n10, "n11": n11})

    diagnostics: list[Diagnostic] = []
    if x == 0 or x == len(viol):
        state = "no violations" if x == 0 else "nothing but violations"
        note = (
            f"Not judged: the sample contains {state}, so the first-order"
            " transition probabilities cannot be estimated and the"
            " independence LR is undefined. Kupiec's coverage test still"
            " applies (and will reject such extreme coverage)."
        )
        diagnostics.append(
            Diagnostic(
                name="christoffersen_independence",
                statistic=0.0,
                p_value=None,
                passed=None,
                interpretation=note,
            )
        )
        diagnostics.append(
            Diagnostic(
                name="christoffersen_conditional_coverage",
                statistic=0.0,
                p_value=None,
                passed=None,
                interpretation=note,
            )
        )
    else:
        pi01 = n01 / (n00 + n01) if n00 + n01 > 0 else 0.0
        pi11 = n11 / (n10 + n11) if n10 + n11 > 0 else 0.0
        pi = (n01 + n11) / (n00 + n01 + n10 + n11)
        ll_null = _xlogy(n00 + n10, 1.0 - pi) + _xlogy(n01 + n11, pi)
        ll_alt = (
            _xlogy(n00, 1.0 - pi01)
            + _xlogy(n01, pi01)
            + _xlogy(n10, 1.0 - pi11)
            + _xlogy(n11, pi11)
        )
        lr_ind = -2.0 * (ll_null - ll_alt)
        p_ind = float(stats.chi2.sf(lr_ind, 1))
        lr_cc = lr_uc + lr_ind
        p_cc = float(stats.chi2.sf(lr_cc, 2))

        no_consecutive = (
            " No consecutive violations occurred (n11 = 0); the LR remains"
            " well defined under the 0*log(0) = 0 convention."
            if n11 == 0.0
            else ""
        )
        diagnostics.append(
            Diagnostic(
                name="christoffersen_independence",
                statistic=lr_ind,
                p_value=p_ind,
                passed=bool(p_ind >= 0.05),
                interpretation="H0: violations are independent over time"
                " (P(violation | violation yesterday) equals P(violation |"
                " none)). passed means no clustering is detected at 5%;"
                " rejection means violations arrive in runs — the VaR ignores"
                " volatility dynamics even if its overall coverage is right."
                + no_consecutive,
            )
        )
        diagnostics.append(
            Diagnostic(
                name="christoffersen_conditional_coverage",
                statistic=lr_cc,
                p_value=p_cc,
                passed=bool(p_cc >= 0.05),
                interpretation="Joint H0: correct coverage AND independent"
                " violations (LR_cc = LR_uc + LR_ind, chi2 with 2 df). passed"
                " means the VaR survives both requirements at 5%.",
            )
        )

    return ResultSet(
        tool="christoffersen_test",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars=scalars,
        manifest=build_manifest(
            data, p, tool="christoffersen_test", version=_VERSION, libraries=_LIBRARIES
        ),
    )
