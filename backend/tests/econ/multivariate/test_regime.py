"""Known-answer tests for the markov_switching tool.

The data is ``make_regime_series`` — validated in ``test_fixtures.py`` — with
a single hard break: 400 observations at vol 0.01 then 400 at vol 0.03, so
the true regime of every observation is known by position. Label switching is
eliminated by the tool's documented convention: regimes are relabeled in
order of increasing variance, so regime_1 is always the calm one.
"""

import math

import pandas as pd
import pytest

import econometrica.econ.multivariate  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_garch_series, make_regime_series

N_LOW = 400
N_HIGH = 400
VOL_LOW = 0.01
VOL_HIGH = 0.03
SEED = 17


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    series = make_regime_series(
        n_low=N_LOW, n_high=N_HIGH, vol_low=VOL_LOW, vol_high=VOL_HIGH, seed=SEED
    )
    return pd.DataFrame({"return": series})


@pytest.fixture(scope="module")
def result(frame: pd.DataFrame) -> ResultSet:
    return run_tool("markov_switching", frame)


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


def test_markov_switching_is_registered_in_the_multivariate_family():
    tool = get_registry().get("markov_switching")
    assert tool.family == "multivariate"
    assert tool.version


def test_regime_variances_bracket_the_truth_in_variance_order(result: ResultSet):
    """Regimes are ordered by variance: regime_1 calm (1e-4), regime_2 wild (9e-4)."""
    low = result.estimate("sigma2.regime_1")
    high = result.estimate("sigma2.regime_2")
    assert low is not None and high is not None
    assert low.value < high.value
    assert low.value == pytest.approx(VOL_LOW**2, rel=0.5)
    assert high.value == pytest.approx(VOL_HIGH**2, rel=0.5)
    for est in (low, high):
        assert est.std_error is not None and est.std_error > 0


def test_regime_means_are_reported_with_inference(result: ResultSet):
    for name in ("mu.regime_1", "mu.regime_2"):
        est = result.estimate(name)
        assert est is not None
        assert est.value == pytest.approx(0.0, abs=0.005)
        assert est.std_error is not None and est.std_error > 0


def test_smoothed_probabilities_classify_at_least_ninety_percent(result: ResultSet):
    """The true regime is known by position: calm first 400, wild last 400."""
    prob_low = result.series["regime_1_prob"].y
    assert len(prob_low) == N_LOW + N_HIGH
    correct = sum(1 for v in prob_low[:N_LOW] if v is not None and v > 0.5)
    correct += sum(1 for v in prob_low[N_LOW:] if v is not None and v < 0.5)
    assert correct >= 0.9 * (N_LOW + N_HIGH)


def test_smoothed_probability_series_sum_to_one(result: ResultSet):
    p1 = result.series["regime_1_prob"].y
    p2 = result.series["regime_2_prob"].y
    for a, b in zip(p1, p2, strict=True):
        assert a is not None and b is not None
        assert a + b == pytest.approx(1.0, abs=1e-9)


def test_smoothed_probability_x_axis_is_iso_dates(result: ResultSet):
    x = result.series["regime_1_prob"].x
    assert x[0] == "2015-01-02"


def test_transition_matrix_rows_are_probability_distributions(result: ResultSet):
    table = result.tables["transition_matrix"]
    assert table.columns == ["from_regime", "to_regime_1", "to_regime_2"]
    assert [row[0] for row in table.rows] == ["regime_1", "regime_2"]
    for row in table.rows:
        probs = row[1:]
        assert all(0.0 <= p <= 1.0 for p in probs)
        assert sum(probs) == pytest.approx(1.0, abs=1e-9)
        # One hard break in 800 observations: both regimes are persistent.
        assert row[1 if row[0] == "regime_1" else 2] > 0.9


def test_expected_durations_are_long_for_persistent_regimes(result: ResultSet):
    for key in ("expected_duration_regime_1", "expected_duration_regime_2"):
        assert result.scalars[key] > 10.0
        assert math.isfinite(result.scalars[key])


def test_markov_switching_reports_fit_statistics(result: ResultSet):
    scalars = result.scalars
    assert scalars["nobs"] == N_LOW + N_HIGH
    assert math.isfinite(scalars["llf"])
    assert scalars["aic"] == pytest.approx(-2.0 * scalars["llf"] + 2.0 * 6)


def test_markov_switching_convergence_diagnostic_passes(result: ResultSet):
    conv = diagnostic(result, "convergence")
    assert conv.passed is True


def test_markov_switching_survives_iid_data():
    """The null case: no regimes at all. The fit must not crash, and the
    reported durations must be finite — near-identical regimes are the
    expected (and correct) degenerate outcome."""
    series = make_garch_series(omega=1e-4, alpha=0.0, beta=0.0, n=600, seed=9)
    result = run_tool("markov_switching", pd.DataFrame({"return": series}))
    assert math.isfinite(result.scalars["llf"])
    for key in ("expected_duration_regime_1", "expected_duration_regime_2"):
        assert math.isfinite(result.scalars[key])
    low = result.estimate("sigma2.regime_1")
    high = result.estimate("sigma2.regime_2")
    assert low is not None and high is not None
    assert low.value <= high.value


def test_markov_switching_results_are_bit_identical_across_runs(frame: pd.DataFrame):
    a = run_tool("markov_switching", frame)
    b = run_tool("markov_switching", frame)
    assert a.scalars == b.scalars
    assert [e.value for e in a.estimates] == [e.value for e in b.estimates]


def test_markov_switching_missing_column_raises_naming_it(frame: pd.DataFrame):
    with pytest.raises(ValueError, match="ret"):
        run_tool("markov_switching", frame, column="ret")


def test_markov_switching_too_few_observations_raises(frame: pd.DataFrame):
    with pytest.raises(ValueError, match="observations"):
        run_tool("markov_switching", frame.iloc[:50])
