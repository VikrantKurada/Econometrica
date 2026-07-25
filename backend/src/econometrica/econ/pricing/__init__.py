"""Asset pricing tool family.

Importing this package registers every pricing tool with the global registry.
"""

from econometrica.econ.pricing import capm, factor_models

__all__ = ["capm", "factor_models"]
