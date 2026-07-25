"""Known-answer tests for the unit root tools (ADF, KPSS, Phillips-Perron).

The fixtures guarantee the truth: ``make_random_walk`` is exactly I(1) and
``make_stationary_ar1`` is exactly stationary, so each test asserts against a
known property rather than a captured output. The KPSS assertions are the
mirror image of the ADF/PP ones on purpose — the reversed null is the point.
"""

import pandas as pd
import pytest

import econometrica.econ.efficiency  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_random_walk, make_stationary_ar1


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


def walk_frame(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"price": make_random_walk(n=n, seed=seed)})


def ar1_frame(phi: float = 0.5, n: int = 2000, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"price": make_stationary_ar1(phi=phi, n=n, seed=seed)})


def sole_diagnostic(result: ResultSet):
    assert len(result.diagnostics) == 1
    return result.diagnostics[0]


def test_unit_root_tools_are_registered_in_the_efficiency_family():
    for name in ("adf", "kpss", "phillips_perron"):
        tool = get_registry().get(name)
        assert tool.family == "efficiency"
        assert tool.version


def test_adf_fails_to_reject_the_unit_root_on_a_random_walk():
    result = run_tool("adf", walk_frame())
    diag = sole_diagnostic(result)
    assert diag.p_value is not None and diag.p_value > 0.10
    assert diag.passed is False  # unit root NOT rejected
    assert {"1%", "5%", "10%"} <= set(diag.critical_values)
    assert result.scalars["nobs"] > 0
    assert result.scalars["lags_used"] >= 0


def test_phillips_perron_agrees_with_adf_on_a_random_walk():
    result = run_tool("phillips_perron", walk_frame())
    diag = sole_diagnostic(result)
    assert diag.p_value is not None and diag.p_value > 0.10
    assert diag.passed is False
    assert {"1%", "5%", "10%"} <= set(diag.critical_values)


def test_kpss_rejects_stationarity_on_a_random_walk():
    """KPSS has the reversed null: a random walk makes it REJECT."""
    result = run_tool("kpss", walk_frame())
    diag = sole_diagnostic(result)
    assert diag.p_value is not None and diag.p_value < 0.05
    assert diag.passed is False  # stationarity rejected
    assert {"1%", "5%", "10%"} <= set(diag.critical_values)


def test_adf_rejects_the_unit_root_on_a_stationary_ar1():
    result = run_tool("adf", ar1_frame())
    diag = sole_diagnostic(result)
    assert diag.p_value is not None and diag.p_value < 0.01
    assert diag.passed is True  # unit root rejected: stationary


def test_phillips_perron_agrees_with_adf_on_a_stationary_ar1():
    result = run_tool("phillips_perron", ar1_frame())
    diag = sole_diagnostic(result)
    assert diag.p_value is not None and diag.p_value < 0.01
    assert diag.passed is True


def test_kpss_does_not_reject_stationarity_on_a_stationary_ar1():
    result = run_tool("kpss", ar1_frame())
    diag = sole_diagnostic(result)
    assert diag.p_value is not None and diag.p_value >= 0.05
    assert diag.passed is True  # stationarity not rejected


def test_differencing_a_random_walk_flips_the_adf_verdict():
    """The defining property of I(1): levels keep the unit root, differences drop it."""
    frame = walk_frame()
    levels = sole_diagnostic(run_tool("adf", frame))
    differenced = sole_diagnostic(run_tool("adf", frame, transform="diff"))
    assert levels.p_value is not None and levels.p_value > 0.10
    assert differenced.p_value is not None and differenced.p_value < 0.01
    assert levels.passed is False and differenced.passed is True


def test_unit_root_statistics_are_bit_identical_across_runs():
    frame = walk_frame()
    for name in ("adf", "kpss", "phillips_perron"):
        a = run_tool(name, frame)
        b = run_tool(name, frame)
        assert sole_diagnostic(a).statistic == sole_diagnostic(b).statistic
        assert sole_diagnostic(a).p_value == sole_diagnostic(b).p_value
        assert a.scalars == b.scalars


def test_passed_semantics_are_documented_per_tool():
    """The ADF/PP vs KPSS asymmetry must be spelled out, not implied."""
    adf = sole_diagnostic(run_tool("adf", walk_frame()))
    kpss = sole_diagnostic(run_tool("kpss", walk_frame()))
    pp = sole_diagnostic(run_tool("phillips_perron", walk_frame()))
    for diag in (adf, pp):
        assert "unit root" in diag.interpretation.lower()
        assert "reject" in diag.interpretation.lower()
    assert "stationar" in kpss.interpretation.lower()
    assert "reject" in kpss.interpretation.lower()


def test_explicit_lags_are_respected_and_reported():
    result = run_tool("adf", walk_frame(), lags=5)
    assert result.scalars["lags_used"] == 5.0


def test_manifest_records_the_tool_and_the_arch_version():
    result = run_tool("adf", walk_frame(n=200))
    assert result.manifest.tool == "adf"
    assert "arch" in result.manifest.library_versions


def test_missing_column_raises_an_error_naming_it():
    frame = walk_frame().rename(columns={"price": "level"})
    with pytest.raises(ValueError, match="price"):
        run_tool("adf", frame)


def test_too_few_observations_raises_an_actionable_error():
    with pytest.raises(ValueError, match="observations"):
        run_tool("adf", walk_frame(n=10))


def test_log_transform_on_nonpositive_values_raises():
    """A driftless walk goes negative, so log transforms must refuse it loudly."""
    with pytest.raises(ValueError, match="positive"):
        run_tool("kpss", walk_frame(), transform="log_diff")
