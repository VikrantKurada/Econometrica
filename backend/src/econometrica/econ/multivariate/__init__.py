"""Multivariate time-series tool family.

Importing this package registers every multivariate tool with the global registry.
"""

from econometrica.econ.multivariate import cointegration, var_model

__all__ = ["cointegration", "var_model"]
