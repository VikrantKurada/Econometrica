"""Multivariate time-series tool family.

Importing this package registers every multivariate tool with the global registry.
"""

from econometrica.econ.multivariate import var_model

__all__ = ["var_model"]
