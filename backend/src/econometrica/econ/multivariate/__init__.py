"""Multivariate time-series tool family.

Importing this package registers every multivariate tool with the global registry.
"""

from econometrica.econ.multivariate import causality, cointegration, regime, var_model

__all__ = ["causality", "cointegration", "regime", "var_model"]
