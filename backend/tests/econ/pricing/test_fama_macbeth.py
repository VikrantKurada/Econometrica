"""Known-answer tests for the Fama-MacBeth cross-sectional risk premium tool."""

import pandas as pd
import pytest

import econometrica.econ.pricing  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_fama_macbeth_panel


def run_fmb(data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get("fama_macbeth")
    return tool.fn(data, tool.params_model(**params))


def test_fama_macbeth_is_registered_in_the_pricing_family():
    assert get_registry().get("fama_macbeth").family == "pricing"


def test_fama_macbeth_recovers_the_true_risk_premium():
    panel = make_fama_macbeth_panel(
        premiums={"exposure": 0.5}, n_entities=30, n_periods=500, seed=31
    )
    result = run_fmb(panel)

    premium = result.estimate("exposure")
    assert premium is not None
    assert premium.value == pytest.approx(0.5, abs=0.01)
    assert premium.ci_low is not None and premium.ci_high is not None
    assert premium.ci_low <= 0.5 <= premium.ci_high
    assert premium.std_error is not None and premium.std_error > 0
    assert premium.p_value is not None and premium.p_value < 0.001

    assert result.scalars["n_entities"] == 30
    assert result.scalars["n_periods"] == 500
    assert result.scalars["nobs"] == 30 * 500


def test_an_exposure_with_zero_true_premium_is_insignificant():
    panel = make_fama_macbeth_panel(
        premiums={"exposure": 0.5, "lucky": 0.0}, n_entities=30, n_periods=500, seed=37
    )
    result = run_fmb(panel, exposures=["exposure", "lucky"])

    lucky = result.estimate("lucky")
    assert lucky is not None
    assert lucky.value == pytest.approx(0.0, abs=0.01)
    assert lucky.p_value is not None and lucky.p_value > 0.01
    priced = result.estimate("exposure")
    assert priced is not None and priced.value == pytest.approx(0.5, abs=0.01)


def test_fama_macbeth_reports_the_intercept():
    panel = make_fama_macbeth_panel(
        premiums={"exposure": 0.5}, n_entities=30, n_periods=500, seed=31, intercept=0.02
    )
    result = run_fmb(panel)
    const = result.estimate("const")
    assert const is not None
    assert const.value == pytest.approx(0.02, abs=0.01)


def test_panel_with_fewer_than_two_periods_raises():
    panel = make_fama_macbeth_panel(premiums={"exposure": 0.5}, n_entities=30, n_periods=1, seed=1)
    with pytest.raises(ValueError, match="period"):
        run_fmb(panel)


def test_frame_without_entity_date_multiindex_raises():
    panel = make_fama_macbeth_panel(
        premiums={"exposure": 0.5}, n_entities=5, n_periods=50, seed=1
    ).reset_index(drop=True)
    with pytest.raises(ValueError, match="MultiIndex"):
        run_fmb(panel)


def test_missing_exposure_column_raises_naming_it():
    panel = make_fama_macbeth_panel(premiums={"exposure": 0.5}, n_entities=5, n_periods=50, seed=1)
    with pytest.raises(ValueError, match="momentum"):
        run_fmb(panel, exposures=["exposure", "momentum"])


def test_fama_macbeth_manifest_is_populated():
    panel = make_fama_macbeth_panel(premiums={"exposure": 0.5}, n_entities=5, n_periods=50, seed=1)
    result = run_fmb(panel)
    assert result.manifest.tool == "fama_macbeth"
    assert "linearmodels" in result.manifest.library_versions
    assert result.manifest.data_fingerprint
