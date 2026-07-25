"""Executable tool preconditions.

The registry's prose `preconditions` tell a model what a tool expects. These
gates refuse when it gets it wrong anyway — which is the only version of the
rule that survives contact with a model that has misread the prose.
"""

import numpy as np
import pandas as pd

import econometrica.econ.multivariate  # registration side-effects
import econometrica.econ.volatility  # noqa: F401  — registration side-effects
from econometrica.econ.gates import evaluate_gates
from econometrica.econ.registry import get_registry
from tests.econ.fixtures import make_garch_series, make_random_walk, make_stationary_ar1


def verdicts(tool_name: str, **series: pd.Series):
    return evaluate_gates(get_registry().get(tool_name), series)


def test_the_garch_family_declares_a_gate_on_arch_effects():
    for name in ("garch", "egarch", "gjr_garch"):
        gates = get_registry().get(name).gates
        assert [gate.check for gate in gates] == ["arch_effects"]
        assert gates[0].expect is True


def test_garch_is_refused_on_a_series_with_no_arch_effects():
    """The named Phase 4 acceptance test.

    An AR(1) has autocorrelation in the mean and none in the variance, so a
    GARCH fitted to it estimates noise — and reports a persistence figure a
    reader will take seriously.
    """
    quiet = make_stationary_ar1(phi=0.5, n=1500, seed=3)

    [verdict] = verdicts("garch", returns=quiet)

    assert verdict.refused is True
    assert verdict.judged is True
    # The refusal carries the tool's own reason, so a user learns from it.
    assert "estimates noise" in verdict.detail


def test_mean_autocorrelation_alone_does_not_read_as_arch_effects():
    """The reason the gate pre-whitens before testing.

    ARCH-LM regresses squared values on their own lags, so an AR(1) with
    homoskedastic innovations fails it on the raw series — and a gate reading
    that would wave through exactly the fit it exists to refuse. Two phi
    values, so this holds for the mechanism rather than for one seed.
    """
    for phi in (0.5, 0.8):
        quiet = make_stationary_ar1(phi=phi, n=1500, seed=11)
        assert verdicts("garch", returns=quiet)[0].refused is True, f"phi={phi}"


def test_garch_is_allowed_on_a_series_that_actually_has_arch_effects():
    clustered = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=1500, seed=3)

    [verdict] = verdicts("garch", returns=clustered)

    assert verdict.allowed is True
    assert verdict.judged is True


def test_a_var_is_refused_on_non_stationary_levels():
    walk = make_random_walk(n=600, seed=5)

    [verdict] = verdicts("var_model", price=walk)

    assert verdict.refused is True
    assert "spurious" in verdict.detail


def test_a_var_is_allowed_on_stationary_series():
    stationary = make_stationary_ar1(phi=0.5, n=600, seed=5)

    [verdict] = verdicts("var_model", x=stationary)

    assert verdict.allowed is True


def test_the_vecm_gate_is_the_mirror_image_of_the_var_gate():
    """Same check, opposite expectation — which is why `expect` exists."""
    walk = make_random_walk(n=600, seed=5)
    stationary = make_stationary_ar1(phi=0.5, n=600, seed=5)

    assert verdicts("vecm", price=walk)[0].allowed is True
    assert verdicts("vecm", x=stationary)[0].refused is True


def test_every_named_column_must_pass():
    """One non-stationary series makes the whole system's dynamics spurious."""
    walk = make_random_walk(n=600, seed=5)
    stationary = make_stationary_ar1(phi=0.5, n=600, seed=6)

    results = verdicts("var_model", good=stationary, bad=walk)

    assert len(results) == 2
    assert any(v.refused for v in results)


def test_a_tool_with_no_gates_is_never_refused():
    assert verdicts("adf", price=make_random_walk(n=200, seed=1)) == []


def test_a_check_that_cannot_run_withholds_judgement_rather_than_refusing():
    """`passed=None` means "not judged", never "failed" — one layer up.

    ARCH-LM needs 20 observations and raises below that. Turning "I could not
    tell" into a refusal would block valid work; turning it into an approval
    would hide that nothing was checked. It does neither.
    """
    [verdict] = verdicts("garch", returns=pd.Series(np.zeros(8)))

    assert verdict.judged is False
    assert verdict.allowed is True
    assert verdict.refused is False
    assert "could not be evaluated" in verdict.detail


def test_a_degenerate_series_is_unjudged_not_refused():
    constant = pd.Series(np.full(300, 5.0))

    [verdict] = verdicts("var_model", flat=constant)

    assert verdict.refused is False
