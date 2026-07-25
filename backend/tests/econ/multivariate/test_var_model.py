"""Known-answer tests for the VAR tools (var_model, irf, fevd).

The data comes from ``make_var1_process``, which iterates the VAR(1)
recursion directly with the committed coefficient matrix
A = [[0.5, 0.3], [0.0, 0.4]] — validated in ``test_fixtures.py`` — so
coefficient recovery, impulse-response shapes and variance decompositions
are genuine known answers:

- A[0][1] = 0.3: lagged y2 moves y1, so ``y1.L1.y2`` is significant and the
  orthogonalized y2->y1 response at horizon 1 is materially nonzero.
- A[1][0] = 0.0: lagged y1 does NOT move y2, so ``y2.L1.y1`` is insignificant,
  the y1->y2 response at horizon 1 is ~0, and y2's forecast-error variance is
  almost entirely its own shock.
- Spectral radius 0.5 < 1: the stability diagnostic passes and every impulse
  response decays toward zero.
"""

import numpy as np
import pandas as pd
import pytest

import econometrica.econ.multivariate  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_var1_process

A = [[0.5, 0.3], [0.0, 0.4]]
N = 2000
SEED = 21


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_var1_process(coef=A, n=N, seed=SEED)


@pytest.fixture(scope="module")
def var_result(frame: pd.DataFrame) -> ResultSet:
    return run_tool("var_model", frame)


@pytest.fixture(scope="module")
def irf_result(frame: pd.DataFrame) -> ResultSet:
    return run_tool("irf", frame)


@pytest.fixture(scope="module")
def fevd_result(frame: pd.DataFrame) -> ResultSet:
    return run_tool("fevd", frame)


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


def test_var_tools_are_registered_in_the_multivariate_family():
    for name in ("var_model", "irf", "fevd"):
        tool = get_registry().get(name)
        assert tool.family == "multivariate"
        assert tool.version


# ---------------------------------------------------------------- var_model


def test_var_model_confidence_intervals_recover_the_coefficient_matrix(
    var_result: ResultSet,
):
    """Estimate names are <equation>.L<lag>.<regressor>; CIs must contain A."""
    for name, true_value in {
        "y1.L1.y1": 0.5,
        "y1.L1.y2": 0.3,
        "y2.L1.y1": 0.0,
        "y2.L1.y2": 0.4,
    }.items():
        est = var_result.estimate(name)
        assert est is not None, f"missing estimate {name!r}"
        assert est.ci_low is not None and est.ci_high is not None
        assert est.ci_low <= true_value <= est.ci_high, name
        assert est.std_error is not None and est.std_error > 0
        assert est.t_stat is not None and est.p_value is not None


def test_var_model_cross_effect_significance_matches_the_truth(var_result: ResultSet):
    """y2 drives y1 (A[0][1] = 0.3); y1 does not drive y2 (A[1][0] = 0)."""
    driven = var_result.estimate("y1.L1.y2")
    absent = var_result.estimate("y2.L1.y1")
    assert driven is not None and absent is not None
    assert driven.p_value is not None and driven.p_value < 0.01
    assert absent.p_value is not None and absent.p_value > 0.05


def test_var_model_selects_lag_one_on_var1_data(var_result: ResultSet):
    assert var_result.scalars["selected_lag"] == 1.0


def test_var_model_reports_fit_statistics(var_result: ResultSet):
    scalars = var_result.scalars
    assert scalars["nobs"] == N - scalars["selected_lag"]
    for key in ("aic", "bic", "hqic"):
        assert np.isfinite(scalars[key])
    assert scalars["bic"] > scalars["aic"]


def test_var_model_stability_diagnostic_passes_on_stationary_data(var_result: ResultSet):
    stability = diagnostic(var_result, "stability")
    assert stability.passed is True
    assert 0.0 < stability.statistic < 1.0
    # Companion eigenvalues of a VAR(1) are the eigenvalues of A: 0.5 and 0.4.
    assert stability.statistic == pytest.approx(0.5, abs=0.1)


def test_var_model_whiteness_diagnostic_passes_on_well_specified_data(
    var_result: ResultSet,
):
    whiteness = diagnostic(var_result, "whiteness")
    assert whiteness.p_value is not None and whiteness.p_value >= 0.05
    assert whiteness.passed is True


def test_var_model_ic_none_fits_exactly_maxlags(frame: pd.DataFrame):
    result = run_tool("var_model", frame, maxlags=3, ic=None)
    assert result.scalars["selected_lag"] == 3.0
    assert result.estimate("y1.L3.y2") is not None


def test_var_model_results_are_bit_identical_across_runs(frame: pd.DataFrame):
    a = run_tool("var_model", frame)
    b = run_tool("var_model", frame)
    assert a.scalars == b.scalars
    assert [e.value for e in a.estimates] == [e.value for e in b.estimates]


def test_var_model_explicit_column_selection_orders_the_system(frame: pd.DataFrame):
    result = run_tool("var_model", frame, columns=["y2", "y1"])
    assert result.estimate("y2.L1.y2") is not None
    assert result.params["columns"] == ["y2", "y1"]


def test_var_model_missing_column_raises_an_error_naming_it(frame: pd.DataFrame):
    with pytest.raises(ValueError, match="y3"):
        run_tool("var_model", frame, columns=["y1", "y3"])


def test_var_model_needs_at_least_two_columns(frame: pd.DataFrame):
    with pytest.raises(ValueError, match="var_model"):
        run_tool("var_model", frame[["y1"]])


def test_var_model_too_few_observations_raises_an_actionable_error():
    small = make_var1_process(coef=A, n=30, seed=SEED)
    with pytest.raises(ValueError, match="observations"):
        run_tool("var_model", small)


def test_var_model_manifest_records_tool_and_statsmodels(var_result: ResultSet):
    assert var_result.manifest.tool == "var_model"
    assert "statsmodels" in var_result.manifest.library_versions


# ---------------------------------------------------------------------- irf


def test_irf_emits_one_series_per_impulse_response_pair(irf_result: ResultSet):
    assert set(irf_result.series) == {"y1->y1", "y1->y2", "y2->y1", "y2->y2"}
    for series in irf_result.series.values():
        assert len(series.y) == 11  # horizons 0..10
        assert series.x[0] == "0" and series.x[-1] == "10"


def test_irf_horizon_zero_own_shock_responses_are_positive(irf_result: ResultSet):
    for name in ("y1->y1", "y2->y2"):
        first = irf_result.series[name].y[0]
        assert first is not None and first > 0


def test_irf_responses_decay_toward_zero_on_stationary_data(irf_result: ResultSet):
    """Spectral radius 0.5: every response at horizon 10 is ~0.5^10 of impact."""
    for series in irf_result.series.values():
        tail = [abs(v) for v in series.y[-3:] if v is not None]
        assert max(tail) < 0.05


def test_irf_orthogonalized_cross_responses_match_the_triangular_structure(
    irf_result: ResultSet,
):
    """A[0][1] = 0.3 channel: y2->y1 at horizon 1 is material; y1->y2 is ~0."""
    y2_to_y1 = irf_result.series["y2->y1"].y[1]
    y1_to_y2 = irf_result.series["y1->y2"].y[1]
    assert y2_to_y1 is not None and y2_to_y1 > 0.15
    assert y1_to_y2 is not None and abs(y1_to_y2) < 0.1


def test_irf_non_orthogonalized_impact_matrix_is_the_identity(frame: pd.DataFrame):
    result = run_tool("irf", frame, orthogonalized=False)
    assert result.series["y1->y1"].y[0] == pytest.approx(1.0)
    assert result.series["y2->y2"].y[0] == pytest.approx(1.0)
    assert result.series["y2->y1"].y[0] == pytest.approx(0.0)
    assert result.series["y1->y2"].y[0] == pytest.approx(0.0)


def test_irf_table_covers_every_horizon_and_pair(irf_result: ResultSet):
    table = irf_result.tables["irf"]
    assert table.columns == ["horizon", "impulse", "response", "irf"]
    assert len(table.rows) == 11 * 2 * 2


def test_irf_custom_horizons(frame: pd.DataFrame):
    result = run_tool("irf", frame, horizons=5)
    assert all(len(s.y) == 6 for s in result.series.values())


# --------------------------------------------------------------------- fevd


def test_fevd_shares_are_in_unit_range_and_sum_to_one(fevd_result: ResultSet):
    """The defining property of a variance decomposition, at every horizon."""
    table = fevd_result.tables["fevd"]
    assert table.columns == ["horizon", "variable", "shock", "share"]
    totals: dict[tuple[object, object], float] = {}
    for horizon, variable, _shock, share in table.rows:
        assert -1e-9 <= share <= 1.0 + 1e-9
        totals[(horizon, variable)] = totals.get((horizon, variable), 0.0) + share
    assert totals, "empty fevd table"
    for total in totals.values():
        assert total == pytest.approx(1.0, abs=1e-9)


def test_fevd_y2_variance_is_mostly_its_own_shock(fevd_result: ResultSet):
    """A is lower-triangular in the causal sense: nothing feeds back into y2."""
    own = fevd_result.series["y2->y2"]
    values = [v for v in own.y if v is not None]
    assert len(values) == 10  # horizons 1..10
    assert min(values) > 0.9


def test_fevd_y2_shock_contributes_to_y1_at_longer_horizons(fevd_result: ResultSet):
    """The 0.3 cross-effect must show up in y1's decomposition as horizons grow."""
    cross = fevd_result.series["y2->y1"]
    last = cross.y[-1]
    assert last is not None and last > 0.02


def test_fevd_series_x_axis_is_the_forecast_horizon(fevd_result: ResultSet):
    series = fevd_result.series["y1->y1"]
    assert series.x[0] == "1" and series.x[-1] == "10"
