"""Randomness tests on a return series: runs, Ljung-Box, BDS and Hurst.

Four complementary looks at "is this sequence unpredictable?":

- ``runs_test``: distribution-free sign test. Implemented directly from the
  Wald-Wolfowitz normal approximation (a few lines) rather than imported from
  ``statsmodels.sandbox``, whose API is explicitly unstable.
- ``ljung_box``: joint autocorrelation test, wrapping
  ``statsmodels.stats.diagnostic.acorr_ljungbox``.
- ``bds``: Brock-Dechert-Scheinkman test of the i.i.d. null against ANY
  dependence, including nonlinear dependence invisible to autocorrelation
  tests — a GARCH series fails BDS while sailing through Ljung-Box. Wraps
  ``statsmodels.tsa.stattools.bds`` (verified under statsmodels 0.14.6:
  ``bds(x, max_dim, epsilon=None, distance=1.5)`` returns statistic and
  p-value arrays for embedding dimensions ``2..max_dim``).
- ``hurst``: rescaled-range (R/S) Hurst exponent with the
  Anis-Lloyd-Peters small-sample correction, implemented directly; the
  algorithm is documented on the tool. Seed-free and deterministic.
"""

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats
from scipy.special import gammaln

from econometrica.econ._common import build_manifest, coerce_params
from econometrica.econ.efficiency._shared import TRANSFORM_FIELD_DOC, Transform, prepare_series
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet, Series, Table

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "scipy", "statsmodels")

_RETURN_COLUMN_DOC = "Column holding the return series under test."


class RunsTestParams(BaseModel):
    """Options for the Wald-Wolfowitz runs test."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    demean: bool = Field(
        default=True,
        description="Take signs relative to the sample mean (True) or to zero (False).",
    )
    min_obs: int = Field(
        default=30, ge=20, description="Minimum observations required after transforming."
    )


class LjungBoxParams(BaseModel):
    """Options for the Ljung-Box autocorrelation test."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    lags: list[int] = Field(
        default=[10], description="Lag counts to test jointly up to (each >= 1)."
    )
    min_obs: int = Field(
        default=30, ge=20, description="Minimum observations required after transforming."
    )


class BdsParams(BaseModel):
    """Options for the BDS independence test."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    dimensions: list[int] = Field(
        default=[2, 3], description="Embedding dimensions to test (each >= 2)."
    )
    distance: float = Field(
        default=1.5,
        gt=0.0,
        description="Proximity threshold epsilon in units of the sample standard deviation.",
    )
    min_obs: int = Field(
        default=100, ge=50, description="Minimum observations required after transforming."
    )


class HurstParams(BaseModel):
    """Options for the rescaled-range Hurst exponent."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    min_window: int = Field(
        default=16, ge=8, description="Smallest R/S block size in the window grid."
    )
    min_obs: int = Field(
        default=256,
        ge=128,
        description="Minimum observations required after transforming; R/S needs"
        " several window sizes with several blocks each.",
    )


@get_registry().register(
    name="runs_test",
    version=_VERSION,
    family="efficiency",
    summary="Wald-Wolfowitz runs test on the signs of returns: too few runs"
    " signals trending, too many signals mean reversion.",
    params_model=RunsTestParams,
    preconditions=(
        "the selected column holds one regularly observed return series; NaNs are dropped",
    ),
)
def runs_test(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, RunsTestParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="runs_test"
    )
    values = series.to_numpy()
    if p.demean:
        values = values - values.mean()
    signs = np.sign(values)
    signs = signs[signs != 0]  # exact zeros carry no sign information

    n_pos = int((signs > 0).sum())
    n_neg = int((signs < 0).sum())
    n = n_pos + n_neg
    if n_pos == 0 or n_neg == 0:
        raise ValueError(
            "runs_test: the series has a single sign"
            f" ({'positive' if n_neg == 0 else 'negative'} only)"
            f" {'after demeaning' if p.demean else ''}; the runs statistic is"
            " undefined — check the column holds returns, or set demean=True"
        )

    n_runs = 1 + int((signs[1:] != signs[:-1]).sum())
    expected = 2.0 * n_pos * n_neg / n + 1.0
    variance = 2.0 * n_pos * n_neg * (2.0 * n_pos * n_neg - n) / (n**2 * (n - 1.0))
    z = (n_runs - expected) / np.sqrt(variance)
    p_value = 2.0 * float(stats.norm.sf(abs(z)))

    diagnostic = Diagnostic(
        name="runs",
        statistic=float(z),
        p_value=p_value,
        passed=bool(p_value >= 0.05),
        interpretation="H0: signs appear in random order. z = (observed runs -"
        " expected)/sd, so z > 0 means MORE runs than expected (negative"
        " dependence, mean reversion) and z < 0 means fewer runs (positive"
        " dependence, trending). passed means randomness is not rejected at 5%.",
    )
    return ResultSet(
        tool="runs_test",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=[diagnostic],
        scalars={
            "nobs": float(len(series)),
            "n_runs": float(n_runs),
            "expected_runs": float(expected),
            "n_pos": float(n_pos),
            "n_neg": float(n_neg),
        },
        manifest=build_manifest(
            data, p, tool="runs_test", version=_VERSION, libraries=_LIBRARIES
        ),
    )


@get_registry().register(
    name="ljung_box",
    version=_VERSION,
    family="efficiency",
    summary="Ljung-Box test that all autocorrelations up to each requested lag"
    " are jointly zero.",
    params_model=LjungBoxParams,
    preconditions=(
        "the selected column holds one regularly observed return series; NaNs are dropped",
    ),
)
def ljung_box(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from statsmodels.stats.diagnostic import acorr_ljungbox

    p = coerce_params(params, LjungBoxParams)
    if not p.lags:
        raise ValueError("ljung_box: the lags list must not be empty")
    if any(lag < 1 for lag in p.lags):
        raise ValueError(f"ljung_box: every lag must be >= 1, got {sorted(p.lags)}")
    lags = sorted(set(p.lags))

    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="ljung_box"
    )
    if lags[-1] >= len(series):
        raise ValueError(
            f"ljung_box: the largest lag ({lags[-1]}) must be smaller than the"
            f" {len(series)} observations; drop the lag or supply more data"
        )

    frame = acorr_ljungbox(series.to_numpy(), lags=lags)
    rows: list[list[object]] = []
    diagnostics: list[Diagnostic] = []
    for lag in lags:
        stat = float(frame.loc[lag, "lb_stat"])
        p_value = float(frame.loc[lag, "lb_pvalue"])
        rows.append([float(lag), stat, p_value])
        diagnostics.append(
            Diagnostic(
                name=f"ljung_box_lag{lag}",
                statistic=stat,
                p_value=p_value,
                passed=bool(p_value >= 0.05),
                interpretation=f"H0: autocorrelations 1..{lag} are jointly zero."
                " passed means no linear predictability is detected at 5%.",
            )
        )

    return ResultSet(
        tool="ljung_box",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars={"nobs": float(len(series)), "n_lags": float(len(lags))},
        tables={"ljung_box": Table(columns=["lag", "lb_stat", "lb_pvalue"], rows=rows)},
        manifest=build_manifest(
            data, p, tool="ljung_box", version=_VERSION, libraries=_LIBRARIES
        ),
    )


@get_registry().register(
    name="bds",
    version=_VERSION,
    family="efficiency",
    summary="BDS test of the i.i.d. null against any dependence, including"
    " nonlinear structure (e.g. GARCH) that autocorrelation tests miss.",
    params_model=BdsParams,
    preconditions=(
        "the selected column holds one regularly observed return series; NaNs are dropped",
    ),
)
def bds_test(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from statsmodels.tsa.stattools import bds

    p = coerce_params(params, BdsParams)
    if not p.dimensions:
        raise ValueError("bds: the dimensions list must not be empty")
    if any(dim < 2 for dim in p.dimensions):
        raise ValueError(f"bds: every embedding dimension must be >= 2, got {sorted(p.dimensions)}")
    dimensions = sorted(set(p.dimensions))

    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="bds"
    )
    statistics, p_values = bds(series.to_numpy(), max_dim=dimensions[-1], distance=p.distance)
    statistics = np.atleast_1d(statistics)  # max_dim=2 squeezes to a scalar
    p_values = np.atleast_1d(p_values)

    rows: list[list[object]] = []
    diagnostics: list[Diagnostic] = []
    for dim in dimensions:
        stat = float(statistics[dim - 2])
        p_value = float(p_values[dim - 2])
        rows.append([float(dim), stat, p_value])
        diagnostics.append(
            Diagnostic(
                name=f"bds_dim{dim}",
                statistic=stat,
                p_value=p_value,
                passed=bool(p_value >= 0.05),
                interpretation=f"H0: the series is i.i.d. (embedding dimension {dim},"
                f" epsilon = {p.distance} sd). passed means independence is not"
                " rejected at 5%; rejection can reflect nonlinear dependence"
                " (e.g. volatility clustering) even when autocorrelations vanish.",
            )
        )

    return ResultSet(
        tool="bds",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars={"nobs": float(len(series)), "n_dimensions": float(len(dimensions))},
        tables={"bds": Table(columns=["dimension", "statistic", "p_value"], rows=rows)},
        manifest=build_manifest(data, p, tool="bds", version=_VERSION, libraries=_LIBRARIES),
    )


def _expected_rs(window: int) -> float:
    """Anis-Lloyd (1976) expected R/S of an i.i.d. series, Peters-corrected.

    For ``window <= 340`` uses the exact gamma-function form (via ``gammaln``
    for numerical stability); beyond that, Stirling's approximation.
    """
    i = np.arange(1, window)
    tail_sum = float(np.sum(np.sqrt((window - i) / i)))
    if window <= 340:
        front = float(np.exp(gammaln((window - 1) / 2.0) - gammaln(window / 2.0)) / np.sqrt(np.pi))
    else:
        front = float(1.0 / np.sqrt(window * np.pi / 2.0))
    return ((window - 0.5) / window) * front * tail_sum


def _window_grid(nobs: int, min_window: int) -> list[int]:
    """Deterministic half-octave grid: ``round(min_window * 2^(k/2)) <= nobs // 4``."""
    sizes: set[int] = set()
    k = 0
    while True:
        window = round(min_window * 2.0 ** (k / 2.0))
        if window > nobs // 4:
            break
        sizes.add(window)
        k += 1
    return sorted(sizes)


@get_registry().register(
    name="hurst",
    version=_VERSION,
    family="efficiency",
    summary="Rescaled-range (R/S) Hurst exponent with the Anis-Lloyd-Peters"
    " small-sample correction: H near 0.5 is random, above 0.5 persistent,"
    " below 0.5 anti-persistent.",
    params_model=HurstParams,
    preconditions=(
        "the selected column holds one regularly observed return series; NaNs are dropped",
    ),
)
def hurst(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    """Corrected R/S estimation, fully deterministic.

    Algorithm: for each window size w on the half-octave grid
    ``round(min_window * 2^(k/2))`` up to ``n // 4``, split the series into
    ``n // w`` non-overlapping blocks; per block compute the rescaled range
    R/S = (max - min of the cumulative demeaned sums) / sample std, and
    average across blocks. The raw estimate is the slope of log mean-R/S on
    log w; the reported H subtracts the same slope computed on the Anis-Lloyd
    expected R/S and adds back 0.5, removing the well-known upward
    small-sample bias of raw R/S (raw estimates on i.i.d. data run 0.55-0.62).
    """
    p = coerce_params(params, HurstParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="hurst"
    )
    values = series.to_numpy()
    nobs = len(values)

    windows = _window_grid(nobs, p.min_window)
    log_w: list[float] = []
    log_rs: list[float] = []
    log_expected: list[float] = []
    mean_rs: list[float | None] = []
    for window in windows:
        blocks = nobs // window
        rs_values = []
        for i in range(blocks):
            block = values[i * window : (i + 1) * window]
            deviations = np.cumsum(block - block.mean())
            spread = float(deviations.max() - deviations.min())
            scale = float(block.std(ddof=1))
            if scale > 0:
                rs_values.append(spread / scale)
        if rs_values:
            log_w.append(float(np.log(window)))
            log_rs.append(float(np.log(np.mean(rs_values))))
            log_expected.append(float(np.log(_expected_rs(window))))
            mean_rs.append(float(np.mean(rs_values)))
        else:
            mean_rs.append(None)

    if len(log_w) < 2:
        raise ValueError(
            f"hurst: only {len(log_w)} usable window sizes on {nobs} observations;"
            " supply more data or lower min_window"
        )
    empirical_slope = float(np.polyfit(log_w, log_rs, 1)[0])
    expected_slope = float(np.polyfit(log_w, log_expected, 1)[0])
    estimate = 0.5 + empirical_slope - expected_slope

    diagnostic = Diagnostic(
        name="hurst",
        statistic=float(estimate),
        p_value=None,
        passed=None,  # no sampling distribution attached: not judged
        interpretation="Anis-Lloyd-Peters corrected R/S Hurst exponent. H near"
        " 0.5 is consistent with an unpredictable series; H > 0.5 signals"
        " persistence (trending), H < 0.5 anti-persistence (mean reversion)."
        " No formal test is attached, so passed is never set here.",
    )
    return ResultSet(
        tool="hurst",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=[diagnostic],
        scalars={
            "hurst": float(estimate),
            "hurst_raw": empirical_slope,
            "nobs": float(nobs),
            "n_windows": float(len(windows)),
        },
        series={
            "rs_by_window": Series(
                name="rs_by_window", x=[str(w) for w in windows], y=mean_rs
            )
        },
        manifest=build_manifest(data, p, tool="hurst", version=_VERSION, libraries=_LIBRARIES),
    )
