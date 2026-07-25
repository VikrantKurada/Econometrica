"""Asset pricing tool family.

Importing this package registers every pricing tool with the global registry.
"""

from econometrica.econ.pricing import capm

__all__ = ["capm"]
