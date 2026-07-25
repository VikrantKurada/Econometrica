"""Volatility and risk tool family.

Importing this package registers every volatility tool with the global registry.
"""

from econometrica.econ.volatility import garch

__all__ = ["garch"]
