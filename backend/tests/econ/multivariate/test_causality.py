"""Known-answer tests for the granger_causality tool.

The causal case is ``make_granger_pair`` — validated in ``test_fixtures.py``:
y depends on x lagged EXACTLY 2 (coefficient 0.8), x is white noise and
unpredictable from y. So x->y must fail to reject at lag 1 (white-noise x
carries no lag-1 information), reject overwhelmingly at lag 2, and y->x must
never reject. The null case is two independent AR(1) series. Seeds were
committed after checking the true-null directions stay above 5% (min-p over 5
unadjusted tests rejects ~22% of the time by chance alone — the very
multiple-testing caveat the tool documents).
"""

import pandas as pd
import pytest

import econometrica.econ.multivariate  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_granger_pair, make_stationary_ar1

N = 1000
SEED = 31


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


@pytest.fixture(scope="module")
def causal_pair() -> pd.DataFrame:
    return make_granger_pair(n=N, seed=SEED)  # coef=0.8 at lag 2


@pytest.fixture(scope="module")
def independent_pair() -> pd.DataFrame:
    a = make_stationary_ar1(phi=0.5, n=N, seed=7)
    b = make_stationary_ar1(phi=0.5, n=N, seed=8)
    return pd.DataFrame({"x": a.to_numpy(), "y": b.to_numpy()}, index=a.index)


@pytest.fixture(scope="module")
def causal_result(causal_pair: pd.DataFrame) -> ResultSet:
    return run_tool("granger_causality", causal_pair)


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


def table_p(result: ResultSet, direction: str, lag: int) -> float:
    for row in result.tables["granger"].rows:
        if row[0] == direction and row[1] == float(lag):
            return float(row[3])
    raise AssertionError(f"no table row for {direction} lag {lag}")


def test_granger_causality_is_registered_in_the_multivariate_family():
    tool = get_registry().get("granger_causality")
    assert tool.family == "multivariate"
    assert tool.version


def test_granger_detects_the_true_direction(causal_result: ResultSet):
    forward = diagnostic(causal_result, "granger_x_to_y")
    assert forward.p_value is not None and forward.p_value < 1e-6
    assert forward.passed is True


def test_granger_does_not_detect_the_reverse_direction(causal_result: ResultSet):
    reverse = diagnostic(causal_result, "granger_y_to_x")
    assert reverse.p_value is not None and reverse.p_value > 0.05
    assert reverse.passed is False


def test_granger_per_lag_table_localises_the_causal_lag(causal_result: ResultSet):
    """White-noise x: no power at lag 1, overwhelming rejection from lag 2 on."""
    assert table_p(causal_result, "x->y", 1) > 0.05
    assert table_p(causal_result, "x->y", 2) < 1e-10
    assert table_p(causal_result, "x->y", 3) < 1e-10


def test_granger_table_covers_both_directions_at_every_lag(causal_result: ResultSet):
    table = causal_result.tables["granger"]
    assert table.columns == ["direction", "lag", "statistic", "p_value"]
    directions = {row[0] for row in table.rows}
    assert directions == {"x->y", "y->x"}
    assert len(table.rows) == 2 * 5  # both directions, maxlag default 5


def test_granger_summary_documents_the_multiple_testing_caveat(
    causal_result: ResultSet,
):
    forward = diagnostic(causal_result, "granger_x_to_y")
    assert "multiple-testing" in forward.interpretation
    assert "H0" in forward.interpretation


def test_granger_neither_direction_rejects_on_independent_series(
    independent_pair: pd.DataFrame,
):
    result = run_tool("granger_causality", independent_pair)
    for name in ("granger_x_to_y", "granger_y_to_x"):
        diag = diagnostic(result, name)
        assert diag.p_value is not None and diag.p_value > 0.05
        assert diag.passed is False


def test_granger_direction_param_restricts_the_output(causal_pair: pd.DataFrame):
    result = run_tool("granger_causality", causal_pair, direction="x_to_y")
    assert [d.name for d in result.diagnostics] == ["granger_x_to_y"]
    assert {row[0] for row in result.tables["granger"].rows} == {"x->y"}


def test_granger_alternative_statistic_is_honoured(causal_pair: pd.DataFrame):
    result = run_tool("granger_causality", causal_pair, statistic="ssr_chi2test")
    forward = diagnostic(result, "granger_x_to_y")
    assert forward.p_value is not None and forward.p_value < 1e-6


def test_granger_maxlag_sets_the_table_depth(causal_pair: pd.DataFrame):
    result = run_tool("granger_causality", causal_pair, maxlag=3)
    assert len(result.tables["granger"].rows) == 2 * 3


def test_granger_custom_column_names(causal_pair: pd.DataFrame):
    renamed = causal_pair.rename(columns={"x": "oil", "y": "airline"})
    result = run_tool("granger_causality", renamed, x="oil", y="airline")
    forward = diagnostic(result, "granger_oil_to_airline")
    assert forward.passed is True


def test_granger_missing_column_raises_naming_it(causal_pair: pd.DataFrame):
    with pytest.raises(ValueError, match="z"):
        run_tool("granger_causality", causal_pair, x="z")


def test_granger_too_few_observations_raises():
    small = make_granger_pair(n=40, seed=SEED)
    with pytest.raises(ValueError, match="observations"):
        run_tool("granger_causality", small)


def test_granger_results_are_bit_identical_across_runs(causal_pair: pd.DataFrame):
    a = run_tool("granger_causality", causal_pair)
    b = run_tool("granger_causality", causal_pair)
    assert a.tables["granger"].rows == b.tables["granger"].rows
