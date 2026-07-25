"""Return construction and calendar alignment.

Every downstream model depends on this; getting log-vs-simple returns or
calendar alignment wrong would silently corrupt everything after it.
"""

from typing import Literal

import numpy as np
import pandas as pd

ReturnMethod = Literal["simple", "log"]

PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4, "A": 1}


def to_returns(prices: pd.Series, method: ReturnMethod = "log") -> pd.Series:
    if method == "simple":
        return prices.pct_change().dropna()
    if method == "log":
        return np.log(prices / prices.shift(1)).dropna()
    raise ValueError(f"unknown return method: {method!r}")


def align_series(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Inner-join series on their shared index, preserving insertion order."""
    frame = pd.concat(series.values(), axis=1, join="inner", keys=series.keys())
    frame.columns = list(series.keys())
    frame = frame.dropna()
    if frame.empty:
        raise ValueError("no overlapping observations across the supplied series")
    return frame


def excess_returns(asset: pd.Series, risk_free: pd.Series) -> pd.Series:
    aligned = align_series({"asset": asset, "rf": risk_free})
    return aligned["asset"] - aligned["rf"]


def annualise_return(period_return: float, periods_per_year: int) -> float:
    return (1.0 + period_return) ** periods_per_year - 1.0
