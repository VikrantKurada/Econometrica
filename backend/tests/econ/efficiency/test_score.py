"""Known-answer tests for the weak_form_efficiency_score composite.

The two poles the composite must separate: a pure random walk (every
component consistent with efficiency) and a price path whose returns are a
strongly autocorrelated AR(1) (every return-predictability component fails
while the unit-root leg — which any cumulated-return path passes — does not).
"""

import pandas as pd
import pytest

import econometrica.econ.efficiency  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_random_walk, make_stationary_ar1

COMPONENTS = {"adf", "kpss", "variance_ratio", "runs", "ljung_box", "hurst"}
TABLE_COLUMNS = ["component", "weight", "sub_score", "statistic", "p_value", "contribution"]


def run_score(data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get("weak_form_efficiency_score")
    merged: dict[str, object] = {"return_method": "diff", **params}
    return tool.fn(data, tool.params_model(**merged))


def walk_frame(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"price": make_random_walk(n=n, seed=seed)})


def ar1_price_frame(phi: float = 0.7, n: int = 2000, seed: int = 1) -> pd.DataFrame:
    """AR(1) returns cumulated to a price path: the canonical inefficient case."""
    returns = make_stationary_ar1(phi=phi, n=n, seed=seed)
    return pd.DataFrame({"price": returns.cumsum()})


def summary_diag(result: ResultSet):
    return next(d for d in result.diagnostics if d.name == "weak_form_efficiency")


def test_score_is_registered_in_the_efficiency_family():
    tool = get_registry().get("weak_form_efficiency_score")
    assert tool.family == "efficiency"
    assert tool.version


def test_a_random_walk_scores_efficient():
    result = run_score(walk_frame())
    assert result.scalars["score"] >= 70.0
    diag = summary_diag(result)
    assert diag.passed is True
    assert "verdict: efficient" in diag.interpretation


def test_ar1_returns_score_inefficient():
    result = run_score(ar1_price_frame())
    assert result.scalars["score"] < 40.0
    diag = summary_diag(result)
    assert diag.passed is False
    assert "verdict: inefficient" in diag.interpretation


def test_the_component_table_sums_to_the_composite():
    result = run_score(walk_frame())
    table = result.tables["components"]
    assert table.columns == TABLE_COLUMNS
    assert {row[0] for row in table.rows} == COMPONENTS
    total = sum(row[5] for row in table.rows)
    assert total == pytest.approx(result.scalars["score"], abs=1e-9)
    for row in table.rows:
        assert 0.0 <= row[2] <= 1.0  # sub-scores live on [0, 1]


def test_one_diagnostic_per_component_plus_the_summary():
    result = run_score(walk_frame())
    names = [d.name for d in result.diagnostics]
    assert set(names) == COMPONENTS | {"weak_form_efficiency"}
    assert len(names) == 7


def test_the_composite_is_invariant_across_runs():
    frame = walk_frame()
    a = run_score(frame)
    b = run_score(frame)
    assert a.scalars == b.scalars
    assert a.tables["components"].rows == b.tables["components"].rows


def test_zero_weight_removes_a_component_contribution():
    result = run_score(walk_frame(), weight_hurst=0.0)
    hurst_row = next(r for r in result.tables["components"].rows if r[0] == "hurst")
    assert hurst_row[1] == 0.0 and hurst_row[5] == 0.0
    total = sum(row[5] for row in result.tables["components"].rows)
    assert total == pytest.approx(result.scalars["score"], abs=1e-9)


def test_weights_can_engineer_a_borderline_verdict():
    """On the AR(1) path the unit-root leg passes (any cumulated stationary
    returns are I(1)) while the return-predictability legs fail; weighting
    only adf, kpss and variance_ratio lands between the thresholds."""
    result = run_score(
        ar1_price_frame(),
        weight_adf=1.0,
        weight_kpss=1.0,
        weight_variance_ratio=1.0,
        weight_runs=0.0,
        weight_ljung_box=0.0,
        weight_hurst=0.0,
    )
    assert 40.0 <= result.scalars["score"] < 70.0
    diag = summary_diag(result)
    assert diag.passed is None  # borderline: neither verdict is asserted
    assert "verdict: borderline" in diag.interpretation


def test_all_zero_weights_raise():
    with pytest.raises(ValueError, match="weight"):
        run_score(
            walk_frame(),
            weight_adf=0.0,
            weight_kpss=0.0,
            weight_variance_ratio=0.0,
            weight_runs=0.0,
            weight_ljung_box=0.0,
            weight_hurst=0.0,
        )


def test_log_diff_on_a_nonpositive_path_raises():
    """The default return_method is log_diff for real prices; a driftless walk
    goes negative and must be refused loudly, not silently NaN-ed."""
    tool = get_registry().get("weak_form_efficiency_score")
    with pytest.raises(ValueError, match="positive"):
        tool.fn(walk_frame(), tool.params_model())


def test_too_few_observations_raise_an_actionable_error():
    with pytest.raises(ValueError, match="observations"):
        run_score(walk_frame(n=200))


def test_missing_column_raises_an_error_naming_it():
    with pytest.raises(ValueError, match="price"):
        run_score(walk_frame().rename(columns={"price": "level"}))
