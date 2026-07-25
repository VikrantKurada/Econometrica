"""Risk metrics: VaR (historical and parametric), CVaR, EWMA and realized
volatility, and drawdown analysis.

Sign convention (shared by every VaR/CVaR tool here and by the backtests):
VaR and CVaR are reported as POSITIVE loss magnitudes — VaR95 = 0.023 means
"5% chance of losing more than 2.3% in one period". When even the tail
quantile of the return distribution is a gain, the reported VaR is negative;
that is the convention carried through, not an error.

Quantile convention: historical VaR and CVaR use ``numpy.quantile`` with
``method='lower'``, i.e. the reported quantile is an actual observed order
statistic (on 1000 points at 95% confidence, exactly the 50th smallest
return), never an interpolation between two observations. Two consequences
are load-bearing: the known answer is exact, and CVaR >= VaR holds as an
identity because the tail mean averages values at or below that order
statistic.
"""

import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import (
    TRANSFORM_FIELD_DOC,
    Transform,
    build_manifest,
    coerce_params,
    prepare_series,
    series_from,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.returns import PERIODS_PER_YEAR
from econometrica.econ.types import ResultSet, Table

_VERSION = "1.0.0"
_VAR_LIBRARIES = ("numpy", "pandas", "scipy")
_VOL_LIBRARIES = ("numpy", "pandas")

_CONFIDENCE_DOC = (
    "Confidence level c: the reported VaR is the loss exceeded with"
    " probability 1 - c (0.95 means a 5% tail)."
)
_RETURN_COLUMN_DOC = "Column holding per-period returns in DECIMAL units."


class _VarParams(BaseModel):
    """Fields shared by the VaR/CVaR tools."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    confidence: float = Field(default=0.95, gt=0.5, lt=1.0, description=_CONFIDENCE_DOC)
    min_obs: int = Field(
        default=100,
        ge=20,
        description="Minimum observations required after transforming; tail"
        " estimates on short samples are noise.",
    )


class HistoricalVarParams(_VarParams):
    """Options for the empirical-quantile VaR."""


class ParametricVarParams(_VarParams):
    """Options for the distribution-based VaR."""

    dist: Literal["normal", "t"] = Field(
        default="normal",
        description="Distribution fitted to the returns: 'normal' (sample"
        " mean/std) or Student's 't' (maximum likelihood; captures fat tails).",
    )
    zero_mean: bool = Field(
        default=False,
        description="Fix the location at zero: the normal variant then uses the"
        " root mean square of returns as sigma, the t variant fits with the"
        " location pinned at 0. Standard for short-horizon VaR.",
    )


class CvarParams(_VarParams):
    """Options for the expected shortfall (CVaR)."""


class EwmaVolParams(BaseModel):
    """Options for the RiskMetrics EWMA volatility."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    lambda_: float = Field(
        default=0.94,
        gt=0.0,
        lt=1.0,
        description="EWMA decay: sigma2_t = lambda * sigma2_{t-1} +"
        " (1 - lambda) * r_t^2, seeded at sigma2_1 = r_1^2. 0.94 is the"
        " RiskMetrics daily standard.",
    )
    frequency: Literal["D", "W", "M", "Q", "A"] = Field(
        default="D", description="Return frequency, used to annualise the current vol."
    )
    min_obs: int = Field(
        default=30, ge=3, description="Minimum observations required after transforming."
    )


class RealizedVolParams(BaseModel):
    """Options for rolling realized volatility."""

    column: str = Field(default="return", description=_RETURN_COLUMN_DOC)
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    window: int = Field(default=21, ge=2, description="Rolling window length in observations.")
    demean: bool = Field(
        default=False,
        description="False (the realized-vol convention): rolling root mean"
        " square of returns. True: rolling sample standard deviation (ddof=1).",
    )
    frequency: Literal["D", "W", "M", "Q", "A"] = Field(
        default="D", description="Return frequency, used to annualise the vol series."
    )
    min_obs: int = Field(
        default=30, ge=5, description="Minimum observations required after transforming."
    )


class DrawdownParams(BaseModel):
    """Options for running-maximum drawdown analysis."""

    column: str = Field(
        default="price", description="Column holding the price path or the returns."
    )
    input_type: Literal["prices", "returns"] = Field(
        default="prices",
        description="'prices' uses the column as a strictly positive level"
        " path; 'returns' compounds simple decimal returns into a wealth"
        " index via cumprod(1 + r).",
    )
    min_obs: int = Field(default=2, ge=2, description="Minimum observations required.")


def _lower_quantile(values: np.ndarray, tail: float) -> float:
    """The observed order statistic at probability ``tail`` (never interpolated)."""
    return float(np.quantile(values, tail, method="lower"))


@get_registry().register(
    name="historical_var",
    version=_VERSION,
    family="volatility",
    summary="Historical (empirical-quantile) Value at Risk, reported as a"
    " POSITIVE LOSS magnitude; the quantile is an actual observed order"
    " statistic (method='lower'), never an interpolation.",
    params_model=HistoricalVarParams,
    preconditions=(
        "the selected column holds one regularly observed decimal return series; NaNs are dropped",
    ),
)
def historical_var(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, HistoricalVarParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="historical_var"
    )
    values = series.to_numpy()
    quantile = _lower_quantile(values, 1.0 - p.confidence)
    n_tail = int((values <= quantile).sum())
    return ResultSet(
        tool="historical_var",
        version=_VERSION,
        params=p.model_dump(),
        scalars={
            "var": -quantile,
            "nobs": float(len(values)),
            "n_tail": float(n_tail),
        },
        manifest=build_manifest(
            data, p, tool="historical_var", version=_VERSION, libraries=_VAR_LIBRARIES
        ),
    )


@get_registry().register(
    name="parametric_var",
    version=_VERSION,
    family="volatility",
    summary="Parametric Value at Risk from a fitted normal or Student-t"
    " distribution, reported as a POSITIVE LOSS magnitude; the t variant"
    " estimates the degrees of freedom by maximum likelihood.",
    params_model=ParametricVarParams,
    preconditions=(
        "the selected column holds one regularly observed decimal return series; NaNs are dropped",
    ),
)
def parametric_var(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from scipy import stats

    p = coerce_params(params, ParametricVarParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="parametric_var"
    )
    values = series.to_numpy()
    tail = 1.0 - p.confidence

    scalars: dict[str, float]
    if p.dist == "normal":
        if p.zero_mean:
            mu, sigma = 0.0, float(np.sqrt(np.mean(values**2)))
        else:
            mu, sigma = float(values.mean()), float(values.std(ddof=1))
        var = -(mu + sigma * float(stats.norm.ppf(tail)))
        scalars = {"var": var, "mu": mu, "sigma": sigma}
    else:
        if p.zero_mean:
            nu, loc, scale = stats.t.fit(values, floc=0.0)
        else:
            nu, loc, scale = stats.t.fit(values)
        var = -float(stats.t.ppf(tail, nu, loc=loc, scale=scale))
        # sigma here is the t SCALE parameter, not the standard deviation
        # (sd = scale * sqrt(nu/(nu-2)) when nu > 2).
        scalars = {"var": var, "mu": float(loc), "sigma": float(scale), "nu": float(nu)}

    scalars["nobs"] = float(len(values))
    return ResultSet(
        tool="parametric_var",
        version=_VERSION,
        params=p.model_dump(),
        scalars=scalars,
        manifest=build_manifest(
            data, p, tool="parametric_var", version=_VERSION, libraries=_VAR_LIBRARIES
        ),
    )


@get_registry().register(
    name="cvar",
    version=_VERSION,
    family="volatility",
    summary="Conditional Value at Risk (expected shortfall): the mean loss in"
    " the tail at or beyond the historical VaR, reported as a POSITIVE LOSS"
    " magnitude; always >= the matching historical VaR.",
    params_model=CvarParams,
    preconditions=(
        "the selected column holds one regularly observed decimal return series; NaNs are dropped",
    ),
)
def cvar(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, CvarParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="cvar"
    )
    values = series.to_numpy()
    quantile = _lower_quantile(values, 1.0 - p.confidence)
    tail_values = values[values <= quantile]  # nonempty: the quantile is an observation
    return ResultSet(
        tool="cvar",
        version=_VERSION,
        params=p.model_dump(),
        scalars={
            "cvar": -float(tail_values.mean()),
            "var": -quantile,
            "n_tail": float(len(tail_values)),
            "nobs": float(len(values)),
        },
        manifest=build_manifest(data, p, tool="cvar", version=_VERSION, libraries=_VAR_LIBRARIES),
    )


@get_registry().register(
    name="ewma_vol",
    version=_VERSION,
    family="volatility",
    summary="RiskMetrics EWMA volatility: sigma2_t = lambda * sigma2_{t-1} +"
    " (1 - lambda) * r_t^2 seeded at r_1^2, reported per period in decimal"
    " units with an annualised current value.",
    params_model=EwmaVolParams,
    preconditions=(
        "the selected column holds one regularly observed decimal return series; NaNs are dropped",
    ),
)
def ewma_vol(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, EwmaVolParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="ewma_vol"
    )
    values = series.to_numpy()

    sigma2 = np.empty(len(values))
    sigma2[0] = values[0] ** 2
    for t in range(1, len(values)):
        sigma2[t] = p.lambda_ * sigma2[t - 1] + (1.0 - p.lambda_) * values[t] ** 2
    vol = pd.Series(np.sqrt(sigma2), index=series.index)

    annualiser = math.sqrt(float(PERIODS_PER_YEAR[p.frequency]))
    return ResultSet(
        tool="ewma_vol",
        version=_VERSION,
        params=p.model_dump(),
        scalars={
            "current_vol": float(vol.iloc[-1]),
            "current_vol_annualized": float(vol.iloc[-1]) * annualiser,
            "nobs": float(len(values)),
        },
        series={"ewma_vol": series_from("ewma_vol", vol)},
        manifest=build_manifest(
            data, p, tool="ewma_vol", version=_VERSION, libraries=_VOL_LIBRARIES
        ),
    )


@get_registry().register(
    name="realized_vol",
    version=_VERSION,
    family="volatility",
    summary="Rolling realized volatility: root mean square of returns over a"
    " trailing window (or rolling sample std with demean=true), per period in"
    " decimal units plus an annualised series.",
    params_model=RealizedVolParams,
    preconditions=(
        "the selected column holds one regularly observed decimal return series; NaNs are dropped",
    ),
)
def realized_vol(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    p = coerce_params(params, RealizedVolParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="realized_vol"
    )
    if p.window > len(series):
        raise ValueError(
            f"realized_vol: window ({p.window}) exceeds the {len(series)}"
            " observations; shrink the window or supply more data"
        )

    if p.demean:
        vol = series.rolling(p.window).std(ddof=1)
    else:
        vol = series.pow(2).rolling(p.window).mean().pow(0.5)

    annualiser = math.sqrt(float(PERIODS_PER_YEAR[p.frequency]))
    full_sample = float(np.sqrt(np.mean(series.to_numpy() ** 2)))
    if p.demean:
        full_sample = float(series.std(ddof=1))
    return ResultSet(
        tool="realized_vol",
        version=_VERSION,
        params=p.model_dump(),
        scalars={
            "current_vol": float(vol.iloc[-1]),
            "current_vol_annualized": float(vol.iloc[-1]) * annualiser,
            "full_sample_vol": full_sample,
            "full_sample_vol_annualized": full_sample * annualiser,
            "nobs": float(len(series)),
        },
        series={
            "realized_vol": series_from("realized_vol", vol),
            "realized_vol_annualized": series_from("realized_vol_annualized", vol * annualiser),
        },
        manifest=build_manifest(
            data, p, tool="realized_vol", version=_VERSION, libraries=_VOL_LIBRARIES
        ),
    )


def _date_label(index: pd.Index, position: int) -> str:
    if isinstance(index, pd.DatetimeIndex):
        return str(index[position].strftime("%Y-%m-%d"))
    return str(index[position])


@get_registry().register(
    name="drawdown",
    version=_VERSION,
    family="volatility",
    summary="Running-maximum drawdown analysis of a price path (or compounded"
    " returns): drawdown series, maximum drawdown with peak/trough/recovery"
    " dates, and the longest underwater spell.",
    params_model=DrawdownParams,
    preconditions=(
        "the selected column holds a strictly positive price path, or decimal"
        " returns with input_type='returns'",
    ),
)
def drawdown(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    """Drawdowns as positive fractions of the running maximum.

    An underwater spell runs from a peak (the last observation at the running
    maximum) to the first observation back at or above it; an unrecovered
    spell runs to the final observation and reports no end date. Durations
    are calendar days between those two index dates when the index is a
    DatetimeIndex, otherwise observation counts.
    """
    p = coerce_params(params, DrawdownParams)
    series = prepare_series(
        data, column=p.column, transform="none", min_obs=p.min_obs, tool="drawdown"
    )

    if p.input_type == "prices":
        if not (series > 0).all():
            raise ValueError(
                "drawdown: prices must be strictly positive; use"
                " input_type='returns' for a return series"
            )
        path = series
    else:
        if not (series > -1.0).all():
            raise ValueError(
                "drawdown: returns must be greater than -1 (a -100% period"
                " destroys the wealth index); check the column holds decimal returns"
            )
        path = (1.0 + series).cumprod()

    running_max = path.cummax()
    dd = 1.0 - path / running_max
    dd_values = dd.to_numpy()
    under = dd_values > 0.0
    n = len(dd_values)

    # Episodes: (peak position, trough position, recovery position or None, depth).
    episodes: list[tuple[int, int, int | None, float]] = []
    i = 0
    while i < n:
        if under[i]:
            start = i - 1  # dd[0] == 0 by construction, so start >= 0
            j = i
            while j < n and under[j]:
                j += 1
            segment = dd_values[i:j]
            trough = i + int(np.argmax(segment))
            end = j if j < n else None
            episodes.append((start, trough, end, float(segment.max())))
            i = j
        else:
            i += 1

    def spell_days(start: int, end: int | None) -> float:
        last = end if end is not None else n - 1
        if isinstance(path.index, pd.DatetimeIndex):
            return float((path.index[last] - path.index[start]).days)
        return float(last - start)

    rows: list[list[object]] = []
    max_dd = 0.0
    if episodes:
        start, trough, end, max_dd = max(episodes, key=lambda e: e[3])
        rows.append(
            [
                _date_label(path.index, start),
                _date_label(path.index, trough),
                _date_label(path.index, end) if end is not None else None,
            ]
        )

    longest = max((spell_days(start, end) for start, _, end, _ in episodes), default=0.0)

    return ResultSet(
        tool="drawdown",
        version=_VERSION,
        params=p.model_dump(),
        scalars={
            "max_drawdown": max_dd,
            "longest_underwater_days": longest,
            "current_drawdown": float(dd_values[-1]),
            "n_drawdowns": float(len(episodes)),
            "nobs": float(n),
        },
        tables={"max_drawdown": Table(columns=["start", "trough", "end"], rows=rows)},
        series={"drawdown": series_from("drawdown", dd)},
        manifest=build_manifest(
            data, p, tool="drawdown", version=_VERSION, libraries=_VOL_LIBRARIES
        ),
    )
