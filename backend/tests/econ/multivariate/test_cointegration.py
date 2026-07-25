"""Known-answer tests for the cointegration tools (johansen, engle_granger, vecm).

The cointegrated case is ``make_cointegrated_pair`` — x a random walk,
y = 1.5x + stationary AR(1) noise, so the true cointegrating vector is
(1, -1.5) and the spread's error-correction speed is phi - 1 = -0.5 in the
y equation. The no-cointegration case is two INDEPENDENT random walks
(different seeds). Both fixtures are validated in ``test_fixtures.py``.
"""

import numpy as np
import pandas as pd
import pytest

import econometrica.econ.multivariate  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_cointegrated_pair, make_random_walk

N = 2000
SEED = 5


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def pair() -> pd.DataFrame:
    x, y = make_cointegrated_pair(n=N, seed=SEED)
    return pd.DataFrame({"y": y, "x": x})


@pytest.fixture(scope="module")
def independent_walks() -> pd.DataFrame:
    a = make_random_walk(n=N, seed=1)
    b = make_random_walk(n=N, seed=2)
    return pd.DataFrame({"y": a.to_numpy(), "x": b.to_numpy()}, index=a.index)


@pytest.fixture(scope="module")
def johansen_pair(pair: pd.DataFrame) -> ResultSet:
    return run_tool("johansen", pair)


@pytest.fixture(scope="module")
def johansen_walks(independent_walks: pd.DataFrame) -> ResultSet:
    return run_tool("johansen", independent_walks)


@pytest.fixture(scope="module")
def eg_pair(pair: pd.DataFrame) -> ResultSet:
    return run_tool("engle_granger", pair)


@pytest.fixture(scope="module")
def vecm_pair(pair: pd.DataFrame) -> ResultSet:
    return run_tool("vecm", pair)


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


def test_cointegration_tools_are_registered_in_the_multivariate_family():
    for name in ("johansen", "engle_granger", "vecm"):
        tool = get_registry().get(name)
        assert tool.family == "multivariate"
        assert tool.version


# ----------------------------------------------------------------- johansen


def test_johansen_determines_rank_one_on_the_cointegrated_pair(johansen_pair: ResultSet):
    assert johansen_pair.scalars["selected_rank"] == 1.0


def test_johansen_determines_rank_zero_on_independent_walks(johansen_walks: ResultSet):
    assert johansen_walks.scalars["selected_rank"] == 0.0


def test_johansen_per_rank_trace_diagnostics_match_the_truth(johansen_pair: ResultSet):
    """H0: rank <= r. r=0 must be rejected (passed), r=1 must not be."""
    r0 = diagnostic(johansen_pair, "johansen_trace_r0")
    r1 = diagnostic(johansen_pair, "johansen_trace_r1")
    assert r0.passed is True
    assert r0.statistic > r0.critical_values["95%"]
    assert r1.passed is False
    assert r1.statistic < r1.critical_values["95%"]


def test_johansen_max_eigenvalue_diagnostics_agree_on_this_fixture(
    johansen_pair: ResultSet,
):
    r0 = diagnostic(johansen_pair, "johansen_max_eig_r0")
    r1 = diagnostic(johansen_pair, "johansen_max_eig_r1")
    assert r0.passed is True
    assert r1.passed is False


def test_johansen_diagnostics_carry_all_three_critical_values(johansen_pair: ResultSet):
    for diag in johansen_pair.diagnostics:
        assert set(diag.critical_values) == {"90%", "95%", "99%"}
        assert (
            diag.critical_values["90%"]
            < diag.critical_values["95%"]
            < diag.critical_values["99%"]
        )


def test_johansen_rank_determination_is_consistent_with_the_table(
    johansen_pair: ResultSet,
):
    """The scalar rank must be the first r whose trace stat is under its 95% cv."""
    table = johansen_pair.tables["rank_tests"]
    assert table.columns[:3] == ["rank_le", "trace_stat", "trace_cv_90"]
    by_rank = {int(row[0]): row for row in table.rows}
    assert len(by_rank) == 2
    assert by_rank[0][1] > by_rank[0][3]  # trace_stat > trace_cv_95 at r=0
    assert by_rank[1][1] < by_rank[1][3]  # trace_stat < trace_cv_95 at r=1


def test_johansen_walks_fail_to_reject_at_rank_zero(johansen_walks: ResultSet):
    r0 = diagnostic(johansen_walks, "johansen_trace_r0")
    assert r0.passed is False
    assert r0.statistic < r0.critical_values["95%"]


def test_johansen_reports_the_normalized_cointegrating_vector(johansen_pair: ResultSet):
    """The determined-rank relation, normalized on the first variable: (1, -1.5)."""
    table = johansen_pair.tables["cointegrating_vectors"]
    assert table.columns == ["variable", "relation_1"]
    vec = {row[0]: row[1] for row in table.rows}
    assert vec["y"] == pytest.approx(1.0)
    assert vec["x"] == pytest.approx(-1.5, abs=0.05)


def test_johansen_needs_at_least_two_columns(pair: pd.DataFrame):
    with pytest.raises(ValueError, match="johansen"):
        run_tool("johansen", pair[["y"]])


def test_johansen_too_few_observations_raises(pair: pd.DataFrame):
    with pytest.raises(ValueError, match="observations"):
        run_tool("johansen", pair.iloc[:40])


# ------------------------------------------------------------ engle_granger


def test_engle_granger_rejects_no_cointegration_on_the_pair(eg_pair: ResultSet):
    eg = diagnostic(eg_pair, "engle_granger")
    assert eg.p_value is not None and eg.p_value < 0.01
    assert eg.passed is True
    assert set(eg.critical_values) == {"1%", "5%", "10%"}


def test_engle_granger_recovers_the_cointegrating_coefficient(eg_pair: ResultSet):
    coef = eg_pair.estimate("x")
    assert coef is not None
    assert coef.value == pytest.approx(1.5, abs=0.05)
    assert eg_pair.estimate("const") is not None


def test_engle_granger_emits_the_equilibrium_spread_series(eg_pair: ResultSet):
    spread = eg_pair.series["spread"]
    assert len(spread.y) == N
    values = np.array([v for v in spread.y if v is not None])
    # The spread is the stationary AR(1) noise: mean ~0, bounded scale.
    assert abs(values.mean()) < 0.5


def test_engle_granger_fails_to_reject_on_independent_walks(
    independent_walks: pd.DataFrame,
):
    result = run_tool("engle_granger", independent_walks)
    eg = diagnostic(result, "engle_granger")
    assert eg.p_value is not None and eg.p_value > 0.05
    assert eg.passed is False


def test_engle_granger_missing_column_raises_naming_it(pair: pd.DataFrame):
    with pytest.raises(ValueError, match="z"):
        run_tool("engle_granger", pair, x=["z"])


# --------------------------------------------------------------------- vecm


def test_vecm_defaults_to_the_johansen_rank(vecm_pair: ResultSet):
    assert vecm_pair.scalars["rank"] == 1.0
    assert vecm_pair.params["rank"] is None  # determined, not supplied


def test_vecm_beta_is_normalized_and_recovers_the_cointegrating_vector(
    vecm_pair: ResultSet,
):
    """beta = (1, -1.5): the leading element is fixed by normalization."""
    beta_y = vecm_pair.estimate("beta.r1.y")
    beta_x = vecm_pair.estimate("beta.r1.x")
    assert beta_y is not None and beta_x is not None
    assert beta_y.value == pytest.approx(1.0)
    assert beta_y.std_error is None  # fixed by normalization, not estimated
    assert beta_x.value == pytest.approx(-1.5, abs=0.05)
    assert beta_x.ci_low is not None and beta_x.ci_high is not None
    assert beta_x.ci_low <= -1.5 <= beta_x.ci_high


def test_vecm_error_correction_is_significantly_negative_in_the_y_equation(
    vecm_pair: ResultSet,
):
    """True speed: phi - 1 = -0.5. y corrects toward equilibrium; x is exogenous."""
    alpha_y = vecm_pair.estimate("alpha.r1.y")
    alpha_x = vecm_pair.estimate("alpha.r1.x")
    assert alpha_y is not None and alpha_x is not None
    assert alpha_y.value == pytest.approx(-0.5, abs=0.15)
    assert alpha_y.p_value is not None and alpha_y.p_value < 0.05
    assert abs(alpha_x.value) < abs(alpha_y.value)


def test_vecm_reports_short_run_dynamics(vecm_pair: ResultSet):
    table = vecm_pair.tables["short_run"]
    assert table.columns == ["equation", "regressor", "coef", "std_error", "t_stat", "p_value"]
    regressors = {row[1] for row in table.rows}
    assert {"L1.d.y", "L1.d.x", "const"} <= regressors
    # 2 equations x (2 lagged diffs + 1 deterministic term)
    assert len(table.rows) == 6


def test_vecm_emits_the_error_correction_term_series(vecm_pair: ResultSet):
    ect = vecm_pair.series["ect_r1"]
    assert len(ect.y) == N
    values = np.array([v for v in ect.y if v is not None])
    # beta'z is the stationary spread; a random walk would wander far from 0.
    assert abs(values.mean()) < 1.0


def test_vecm_explicit_rank_is_respected(pair: pd.DataFrame):
    result = run_tool("vecm", pair, rank=1)
    assert result.scalars["rank"] == 1.0
    assert result.params["rank"] == 1


def test_vecm_raises_on_independent_walks_when_rank_is_determined(
    independent_walks: pd.DataFrame,
):
    with pytest.raises(ValueError, match="rank 0"):
        run_tool("vecm", independent_walks)


def test_vecm_reports_fit_scalars(vecm_pair: ResultSet):
    assert vecm_pair.scalars["nobs"] == N - 2.0  # k_ar_diff=1 plus one difference
    assert np.isfinite(vecm_pair.scalars["llf"])


def test_vecm_results_are_bit_identical_across_runs(pair: pd.DataFrame):
    a = run_tool("vecm", pair)
    b = run_tool("vecm", pair)
    assert a.scalars == b.scalars
    assert [e.value for e in a.estimates] == [e.value for e in b.estimates]
