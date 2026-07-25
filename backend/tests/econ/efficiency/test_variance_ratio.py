"""Known-answer tests for the Lo-MacKinlay variance ratio tool.

Truths the fixtures guarantee: a random walk has VR(q) = 1 at every horizon;
positively autocorrelated AR(1) returns cumulated to a level path have VR > 1
rising with the horizon; negatively autocorrelated returns have VR < 1. And
VR(1) = 1 identically, by construction.
"""

import pandas as pd
import pytest

import econometrica.econ.efficiency  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_random_walk, make_stationary_ar1

TABLE_COLUMNS = ["horizon", "vr", "z_homoskedastic", "z_heteroskedastic", "p_value"]


def run_vr(data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get("variance_ratio")
    return tool.fn(data, tool.params_model(**params))


def walk_frame(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"price": make_random_walk(n=n, seed=seed)})


def cumulated_ar1_frame(phi: float, n: int = 2000, seed: int = 2) -> pd.DataFrame:
    """AR(1) returns cumulated to a level path — what the VR tool tests."""
    returns = make_stationary_ar1(phi=phi, n=n, seed=seed)
    return pd.DataFrame({"price": returns.cumsum()})


def table_by_horizon(result: ResultSet) -> dict[float, list]:
    table = result.tables["variance_ratios"]
    assert table.columns == TABLE_COLUMNS
    return {row[0]: row for row in table.rows}


def test_variance_ratio_is_registered_in_the_efficiency_family():
    tool = get_registry().get("variance_ratio")
    assert tool.family == "efficiency"
    assert tool.version


def test_vr_at_horizon_one_is_exactly_one():
    """VR(1) compares the one-period variance with itself: identically 1."""
    rows = table_by_horizon(run_vr(walk_frame()))
    assert rows[1.0][1] == 1.0
    trending = table_by_horizon(run_vr(cumulated_ar1_frame(phi=0.3)))
    assert trending[1.0][1] == 1.0


def test_random_walk_has_vr_near_one_and_insignificant_at_all_horizons():
    result = run_vr(walk_frame())
    rows = table_by_horizon(result)
    for horizon in (2.0, 4.0, 8.0, 16.0):
        _, vr, _z_hom, _z_het, p_value = rows[horizon]
        assert vr == pytest.approx(1.0, abs=0.1)
        assert p_value > 0.05, f"het-robust VR test rejected a true random walk at q={horizon}"
    for diag in result.diagnostics:
        assert diag.passed is True  # random-walk null not rejected


def test_positively_autocorrelated_returns_have_vr_above_one_rising_with_horizon():
    result = run_vr(cumulated_ar1_frame(phi=0.3))
    rows = table_by_horizon(result)
    previous = 1.0
    for horizon in (2.0, 4.0, 8.0, 16.0):
        _, vr, z_hom, z_het, p_value = rows[horizon]
        assert vr > 1.0
        assert vr > previous, "VR should rise with the horizon under AR(+) returns"
        assert p_value < 0.01
        assert z_het > 0 and z_hom > 0
        previous = vr
    for diag in result.diagnostics:
        assert diag.passed is False


def test_mean_reverting_returns_have_vr_below_one_and_significant():
    result = run_vr(cumulated_ar1_frame(phi=-0.3))
    rows = table_by_horizon(result)
    for horizon in (2.0, 4.0, 8.0, 16.0):
        _, vr, _z_hom, z_het, p_value = rows[horizon]
        assert vr < 1.0
        assert p_value < 0.01
        assert z_het < 0


def test_vr_emits_a_series_matching_the_table():
    result = run_vr(walk_frame())
    series = result.series["vr_by_horizon"]
    rows = table_by_horizon(result)
    assert series.x == ["1", "2", "4", "8", "16"]
    for x, y in zip(series.x, series.y, strict=True):
        assert y == rows[float(x)][1]


def test_the_cumsum_transform_reproduces_precumulated_level_statistics_exactly():
    """A return column under transform='cumsum' tests the same level path."""
    returns = make_random_walk(n=2000, seed=1).diff().dropna()
    from_levels = run_vr(pd.DataFrame({"price": returns.cumsum()}))
    from_returns = run_vr(pd.DataFrame({"ret": returns}), column="ret", transform="cumsum")
    assert (
        from_levels.tables["variance_ratios"].rows
        == from_returns.tables["variance_ratios"].rows
    )


def test_vr_statistics_are_bit_identical_across_runs():
    frame = walk_frame()
    a = run_vr(frame)
    b = run_vr(frame)
    assert a.tables["variance_ratios"].rows == b.tables["variance_ratios"].rows
    assert a.scalars == b.scalars


def test_custom_horizons_are_sorted_and_deduplicated():
    result = run_vr(walk_frame(), horizons=[8, 2, 8])
    rows = result.tables["variance_ratios"].rows
    assert [row[0] for row in rows] == [1.0, 2.0, 8.0]


def test_horizons_below_two_raise():
    with pytest.raises(ValueError, match="horizon"):
        run_vr(walk_frame(), horizons=[1, 2])


def test_too_few_observations_for_the_largest_horizon_raise():
    with pytest.raises(ValueError, match="observations"):
        run_vr(walk_frame(n=150), horizons=[2, 128], min_obs=100)


def test_missing_column_raises_an_error_naming_it():
    with pytest.raises(ValueError, match="price"):
        run_vr(walk_frame().rename(columns={"price": "level"}))
