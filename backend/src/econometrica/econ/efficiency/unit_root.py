"""Unit root and stationarity tests: ADF, KPSS and Phillips-Perron.

All three wrap ``arch.unitroot`` for a consistent implementation and share
Schwert's deterministic lag rule as the default, because arch 8's automatic
lag selection is data-dependent (and deprecated for KPSS): reproducibility
demands an explicit, documented rule.

``passed`` semantics are asymmetric BY DESIGN and documented per diagnostic:

- ADF / Phillips-Perron: H0 is a unit root, so ``passed`` means the unit root
  is REJECTED at 5% — the series looks stationary.
- KPSS: H0 is stationarity (the reverse null), so ``passed`` means
  stationarity is NOT rejected at 5%.

A random walk therefore fails all three checks, and a stationary series
passes all three: agreement between the reversed nulls is the evidence.
"""

from typing import Literal, Protocol

import pandas as pd
from pydantic import BaseModel, Field

from econometrica.econ._common import build_manifest, coerce_params
from econometrica.econ.efficiency._shared import (
    TRANSFORM_FIELD_DOC,
    Transform,
    prepare_series,
    schwert_lags,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet

_VERSION = "1.0.0"
_LIBRARIES = ("arch", "numpy", "pandas")


class _UnitRootParams(BaseModel):
    """Fields shared by the three unit root tools."""

    column: str = Field(
        default="price",
        description="Column holding the series under test (typically price levels).",
    )
    transform: Transform = Field(default="none", description=TRANSFORM_FIELD_DOC)
    lags: int | None = Field(
        default=None,
        ge=0,
        description="Lag count (ADF) or long-run variance bandwidth (KPSS/PP)."
        " None applies Schwert's deterministic rule floor(12*(n/100)^(1/4)).",
    )
    min_obs: int = Field(
        default=30, ge=15, description="Minimum observations required after transforming."
    )


class AdfParams(_UnitRootParams):
    """Options for the Augmented Dickey-Fuller test."""

    trend: Literal["n", "c", "ct"] = Field(
        default="c",
        description="Deterministic terms: 'n' none, 'c' constant, 'ct' constant plus trend.",
    )


class KpssParams(_UnitRootParams):
    """Options for the KPSS test (arch supports only 'c' and 'ct' trends)."""

    trend: Literal["c", "ct"] = Field(
        default="c",
        description="Null hypothesis: level stationarity ('c') or trend stationarity ('ct').",
    )


class PhillipsPerronParams(_UnitRootParams):
    """Options for the Phillips-Perron test."""

    trend: Literal["n", "c", "ct"] = Field(
        default="c",
        description="Deterministic terms: 'n' none, 'c' constant, 'ct' constant plus trend.",
    )


class _ArchUnitRootResult(Protocol):
    """The slice of arch's unit root result objects the tools consume."""

    @property
    def stat(self) -> float: ...
    @property
    def pvalue(self) -> float: ...
    @property
    def critical_values(self) -> dict[str, float]: ...
    @property
    def lags(self) -> int: ...
    @property
    def nobs(self) -> int: ...


def _unit_root_result(
    data: pd.DataFrame,
    params: _UnitRootParams,
    fitted: _ArchUnitRootResult,
    *,
    tool: str,
    passed: bool,
    interpretation: str,
) -> ResultSet:
    diagnostic = Diagnostic(
        name=tool,
        statistic=float(fitted.stat),
        p_value=float(fitted.pvalue),
        critical_values={k: float(v) for k, v in fitted.critical_values.items()},
        passed=passed,
        interpretation=interpretation,
    )
    return ResultSet(
        tool=tool,
        version=_VERSION,
        params=params.model_dump(),
        diagnostics=[diagnostic],
        scalars={"nobs": float(fitted.nobs), "lags_used": float(fitted.lags)},
        manifest=build_manifest(data, params, tool=tool, version=_VERSION, libraries=_LIBRARIES),
    )


@get_registry().register(
    name="adf",
    version=_VERSION,
    family="efficiency",
    summary="Augmented Dickey-Fuller unit root test (H0: unit root); rejection"
    " means the series looks stationary.",
    params_model=AdfParams,
    preconditions=(
        "the selected column holds one regularly observed time series; NaNs are dropped",
    ),
)
def adf(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from arch.unitroot import ADF

    p = coerce_params(params, AdfParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="adf"
    )
    lags = p.lags if p.lags is not None else schwert_lags(len(series))
    fitted = ADF(series.to_numpy(), lags=lags, trend=p.trend)
    return _unit_root_result(
        data,
        p,
        fitted,
        tool="adf",
        passed=bool(fitted.pvalue < 0.05),
        interpretation="H0: the series has a unit root (is nonstationary). passed"
        " means the unit root is rejected at 5%, i.e. the series looks stationary."
        " Note the asymmetry with KPSS, whose null is stationarity.",
    )


@get_registry().register(
    name="kpss",
    version=_VERSION,
    family="efficiency",
    summary="KPSS stationarity test (H0: stationarity — the REVERSE of the"
    " ADF/PP null); rejection means the series looks nonstationary.",
    params_model=KpssParams,
    preconditions=(
        "the selected column holds one regularly observed time series; NaNs are dropped",
    ),
)
def kpss(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from arch.unitroot import KPSS

    p = coerce_params(params, KpssParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="kpss"
    )
    lags = p.lags if p.lags is not None else schwert_lags(len(series))
    fitted = KPSS(series.to_numpy(), lags=lags, trend=p.trend)
    return _unit_root_result(
        data,
        p,
        fitted,
        tool="kpss",
        passed=bool(fitted.pvalue >= 0.05),
        interpretation="H0: the series is stationary — the REVERSE of the ADF/PP"
        " null. passed means stationarity is NOT rejected at 5%. A random walk"
        " should fail this check while failing to reject under ADF/PP.",
    )


@get_registry().register(
    name="phillips_perron",
    version=_VERSION,
    family="efficiency",
    summary="Phillips-Perron unit root test (H0: unit root) with a"
    " nonparametric correction for serial correlation and heteroskedasticity.",
    params_model=PhillipsPerronParams,
    preconditions=(
        "the selected column holds one regularly observed time series; NaNs are dropped",
    ),
)
def phillips_perron(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    from arch.unitroot import PhillipsPerron

    p = coerce_params(params, PhillipsPerronParams)
    series = prepare_series(
        data, column=p.column, transform=p.transform, min_obs=p.min_obs, tool="phillips_perron"
    )
    lags = p.lags if p.lags is not None else schwert_lags(len(series))
    fitted = PhillipsPerron(series.to_numpy(), lags=lags, trend=p.trend)
    return _unit_root_result(
        data,
        p,
        fitted,
        tool="phillips_perron",
        passed=bool(fitted.pvalue < 0.05),
        interpretation="H0: the series has a unit root (is nonstationary). passed"
        " means the unit root is rejected at 5%, i.e. the series looks stationary."
        " Note the asymmetry with KPSS, whose null is stationarity.",
    )
