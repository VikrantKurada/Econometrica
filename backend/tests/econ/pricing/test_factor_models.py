"""Known-answer tests for the ff3 / carhart4 / ff5 factor model tools."""

import pandas as pd
import pytest

import econometrica.econ.pricing  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_factor_data

FF3 = {"mkt_rf": 1.1, "smb": 0.4, "hml": -0.3}
CARHART4 = {"mkt_rf": 1.0, "smb": 0.5, "hml": -0.2, "mom": 0.0}
FF5 = {"mkt_rf": 1.0, "smb": 0.4, "hml": -0.3, "rmw": 0.2, "cma": -0.1}


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


@pytest.mark.parametrize("name", ["ff3", "carhart4", "ff5"])
def test_factor_tools_are_registered_in_the_pricing_family(name: str):
    tool = get_registry().get(name)
    assert tool.family == "pricing"


@pytest.mark.parametrize(
    ("name", "loadings", "seed"),
    [("ff3", FF3, 13), ("carhart4", CARHART4, 1), ("ff5", FF5, 19)],
)
def test_factor_models_recover_true_loadings_within_ci(
    name: str, loadings: dict[str, float], seed: int
):
    data = make_factor_data(loadings=loadings, alpha=0.0002, n=5000, seed=seed, resid_vol=0.005)
    result = run_tool(name, data)

    for factor, true_loading in loadings.items():
        est = result.estimate(factor)
        assert est is not None, f"missing estimate for {factor}"
        assert est.value == pytest.approx(true_loading, abs=0.05)
        assert est.ci_low is not None and est.ci_high is not None
        assert est.ci_low <= true_loading <= est.ci_high
    alpha = result.estimate("alpha")
    assert alpha is not None
    assert alpha.value == pytest.approx(0.0002, abs=0.0005)
    assert result.scalars["nobs"] == 5000
    assert 0.0 < result.scalars["r_squared"] <= 1.0


def test_a_factor_with_zero_true_loading_is_insignificant():
    data = make_factor_data(loadings=CARHART4, alpha=0.0002, n=5000, seed=1, resid_vol=0.005)
    result = run_tool("carhart4", data)
    mom = result.estimate("mom")
    assert mom is not None
    assert mom.p_value is not None and mom.p_value > 0.01
    assert mom.t_stat is not None and abs(mom.t_stat) < 2.58


def test_missing_factor_column_raises_naming_the_column():
    data = make_factor_data(loadings=FF3, alpha=0.0, n=500, seed=13).drop(columns=["hml"])
    with pytest.raises(ValueError, match="hml"):
        run_tool("ff3", data)


def test_factor_model_emits_residuals_diagnostics_and_manifest():
    data = make_factor_data(loadings=FF3, alpha=0.0002, n=2000, seed=13)
    result = run_tool("ff3", data)

    assert len(result.series["residuals"].y) == 2000
    assert {d.name for d in result.diagnostics} >= {"jarque_bera", "durbin_watson"}
    assert result.manifest.tool == "ff3"
    assert "statsmodels" in result.manifest.library_versions
    alpha = result.estimate("alpha")
    assert alpha is not None
    expected = (1.0 + alpha.value) ** 252 - 1.0
    assert result.scalars["alpha_annualised"] == pytest.approx(expected)


def test_different_factor_sets_hash_to_different_params():
    data = make_factor_data(loadings=CARHART4, alpha=0.0, n=500, seed=17)
    ff3_result = run_tool("ff3", data)
    carhart_result = run_tool("carhart4", data)
    assert ff3_result.manifest.params_hash != carhart_result.manifest.params_hash
    assert ff3_result.manifest.data_fingerprint == carhart_result.manifest.data_fingerprint


def test_too_few_observations_raises_an_actionable_error():
    data = make_factor_data(loadings=FF3, alpha=0.0, n=10, seed=13)
    with pytest.raises(ValueError, match="observations"):
        run_tool("ff3", data)
