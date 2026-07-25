"""The econometrics core.

Deliberately empty of imports at module level: ``econ.types`` and
``econ.registry`` are cheap, while the tool families drag in statsmodels, arch
and linearmodels. Anything that only needs a ``ResultSet`` should not pay for
that.
"""


def load_tools() -> None:
    """Register every shipped tool with the global registry.

    Registration is an import side-effect of the five family packages, and
    until Phase 4 nothing in the running application imported any of them — so
    a live server's registry was empty while the test suite's was full,
    because each test module imports the family it exercises. Anything that
    resolves a tool *by name* has to call this first.

    Repeat calls cost nothing; the imports are already in ``sys.modules``.
    """
    from econometrica.econ import (  # noqa: F401 — registration side-effects
        efficiency,
        events,
        multivariate,
        pricing,
        volatility,
    )
