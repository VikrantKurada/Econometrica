"""Helpers specific to the market efficiency tool family.

The series preparation helpers (``Transform``, ``prepare_series``) that
originated here were promoted to :mod:`econometrica.econ._common` on third
use (the volatility family). What remains is genuinely efficiency-specific.
"""

import math


def schwert_lags(nobs: int) -> int:
    """Schwert's (1989) deterministic lag rule: ``floor(12 * (n/100)^(1/4))``.

    Used as the default lag/bandwidth for all three unit root tools so results
    never depend on the library's data-dependent selection, which both changes
    across library versions and emits deprecation warnings under arch 8.
    """
    if nobs <= 0:
        raise ValueError(f"nobs must be positive, got {nobs}")
    scaled: float = 12.0 * (nobs / 100.0) ** 0.25
    return math.floor(scaled)
