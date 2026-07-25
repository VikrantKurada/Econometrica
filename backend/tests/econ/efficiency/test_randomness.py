"""Known-answer tests for the randomness tools: runs, Ljung-Box, BDS, Hurst.

The canonical pair: the increments of a validated random walk are i.i.d. by
construction (every randomness null holds), while the walk's LEVELS are
maximally persistent (Hurst near 1). GARCH returns are the reason BDS ships:
serially uncorrelated (Ljung-Box blind) yet nonlinearly dependent (BDS sees).

Deviation from the plan spec: a strictly alternating sign sequence produces
MORE runs than expected, so the runs z statistic is POSITIVE (the plan said
z < 0 while also saying "more runs than expected"; the sign convention
z = (observed - expected)/sd makes those incompatible, and positive is right).
"""

import pandas as pd
import pytest

import econometrica.econ.efficiency  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_garch_series, make_random_walk, make_stationary_ar1


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


def iid_frame(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    """Increments of a validated random walk: i.i.d. Gaussian by construction."""
    return pd.DataFrame({"return": make_random_walk(n=n, seed=seed).diff().dropna()})


def walk_levels_frame(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"price": make_random_walk(n=n, seed=seed)})


def ar1_frame(phi: float = 0.7, n: int = 2000, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"return": make_stationary_ar1(phi=phi, n=n, seed=seed)})


def garch_frame(n: int = 2000, seed: int = 3) -> pd.DataFrame:
    series = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=n, seed=seed)
    return pd.DataFrame({"return": series})


def test_randomness_tools_are_registered_in_the_efficiency_family():
    for name in ("runs_test", "ljung_box", "bds", "hurst"):
        tool = get_registry().get(name)
        assert tool.family == "efficiency"
        assert tool.version


# ---------------------------------------------------------------- runs_test


def test_runs_test_is_insignificant_on_iid_noise():
    result = run_tool("runs_test", iid_frame())
    diag = result.diagnostics[0]
    assert diag.p_value is not None and diag.p_value > 0.05
    assert abs(diag.statistic) < 2.0
    assert diag.passed is True
    assert result.scalars["n_runs"] > 0
    assert result.scalars["n_pos"] + result.scalars["n_neg"] <= result.scalars["nobs"]


def test_runs_test_rejects_an_alternating_sequence_with_positive_z():
    """Alternating signs give the maximum number of runs: z must be POSITIVE."""
    data = pd.DataFrame({"return": [1.0, -1.0] * 300})
    result = run_tool("runs_test", data, demean=False)
    diag = result.diagnostics[0]
    assert diag.statistic > 0  # more runs than expected under randomness
    assert diag.p_value is not None and diag.p_value < 1e-10
    assert diag.passed is False
    assert result.scalars["n_runs"] > result.scalars["expected_runs"]


def test_runs_test_sees_fewer_runs_under_positive_autocorrelation():
    result = run_tool("runs_test", ar1_frame(phi=0.7))
    diag = result.diagnostics[0]
    assert diag.statistic < 0  # trending series: fewer, longer runs
    assert diag.p_value is not None and diag.p_value < 1e-10
    assert result.scalars["n_runs"] < result.scalars["expected_runs"]


def test_runs_test_documents_its_sign_convention():
    diag = run_tool("runs_test", iid_frame()).diagnostics[0]
    assert "more runs" in diag.interpretation.lower()


def test_runs_test_refuses_a_degenerate_single_sign_series():
    data = pd.DataFrame({"return": [1.0] * 100})
    with pytest.raises(ValueError, match="sign"):
        run_tool("runs_test", data, demean=False)


# ---------------------------------------------------------------- ljung_box


def test_ljung_box_is_insignificant_on_iid_noise():
    result = run_tool("ljung_box", iid_frame())
    table = result.tables["ljung_box"]
    assert table.columns == ["lag", "lb_stat", "lb_pvalue"]
    assert len(table.rows) == 1 and table.rows[0][0] == 10.0
    assert table.rows[0][2] > 0.05
    assert result.diagnostics[0].passed is True


def test_ljung_box_rejects_hard_on_ar1_returns():
    result = run_tool("ljung_box", ar1_frame(phi=0.7), lags=[5, 10, 20])
    table = result.tables["ljung_box"]
    assert [row[0] for row in table.rows] == [5.0, 10.0, 20.0]
    for row, diag in zip(table.rows, result.diagnostics, strict=True):
        assert row[2] < 1e-6
        assert diag.p_value is not None and diag.p_value < 1e-6
        assert diag.passed is False


# ---------------------------------------------------------------------- bds


def test_bds_fails_to_reject_iid_noise():
    result = run_tool("bds", iid_frame())
    table = result.tables["bds"]
    assert table.columns == ["dimension", "statistic", "p_value"]
    assert [row[0] for row in table.rows] == [2.0, 3.0]
    for row, diag in zip(table.rows, result.diagnostics, strict=True):
        assert row[2] > 0.05
        assert diag.passed is True


def test_bds_rejects_garch_even_though_ljung_box_does_not():
    """The reason BDS ships: nonlinear dependence that autocorrelation misses."""
    garch = garch_frame()
    bds_result = run_tool("bds", garch)
    lb_result = run_tool("ljung_box", garch)
    for diag in bds_result.diagnostics:
        assert diag.p_value is not None and diag.p_value < 0.01
        assert diag.passed is False
    lb_diag = lb_result.diagnostics[0]
    assert lb_diag.p_value is not None and lb_diag.p_value > 0.05


def test_bds_dimensions_below_two_raise():
    with pytest.raises(ValueError, match="dimension"):
        run_tool("bds", iid_frame(), dimensions=[1, 2])


# -------------------------------------------------------------------- hurst


def test_hurst_is_near_half_on_iid_noise():
    result = run_tool("hurst", iid_frame())
    assert result.scalars["hurst"] == pytest.approx(0.5, abs=0.1)


def test_hurst_is_near_one_on_random_walk_levels():
    """The canonical pair: same fixture, levels instead of increments."""
    result = run_tool("hurst", walk_levels_frame(), column="price")
    assert result.scalars["hurst"] == pytest.approx(1.0, abs=0.1)


def test_hurst_emits_the_rs_by_window_series():
    result = run_tool("hurst", iid_frame())
    series = result.series["rs_by_window"]
    assert len(series.x) == result.scalars["n_windows"] >= 5
    assert all(y is not None and y > 0 for y in series.y)
    # window sizes ascend
    assert [int(x) for x in series.x] == sorted(int(x) for x in series.x)


def test_hurst_leaves_passed_unjudged():
    """No sampling distribution is attached, so passed must be None, not False."""
    diag = run_tool("hurst", iid_frame()).diagnostics[0]
    assert diag.passed is None
    assert diag.statistic == pytest.approx(0.5, abs=0.1)


def test_hurst_requires_a_long_series():
    with pytest.raises(ValueError, match="observations"):
        run_tool("hurst", iid_frame(n=100))


# ------------------------------------------------------------------- shared


def test_randomness_statistics_are_bit_identical_across_runs():
    frame = iid_frame()
    for name in ("runs_test", "ljung_box", "bds", "hurst"):
        a = run_tool(name, frame)
        b = run_tool(name, frame)
        assert a.scalars == b.scalars
        assert [d.statistic for d in a.diagnostics] == [d.statistic for d in b.diagnostics]


def test_missing_column_raises_an_error_naming_it():
    frame = iid_frame().rename(columns={"return": "ret"})
    for name in ("runs_test", "ljung_box", "bds", "hurst"):
        with pytest.raises(ValueError, match="return"):
            run_tool(name, frame)
