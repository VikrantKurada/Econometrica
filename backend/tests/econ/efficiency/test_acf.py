"""Autocorrelation and partial autocorrelation with Bartlett bands.

Added in Phase 5 rather than Phase 2: the design's chart list asks for ACF and
PACF stems, and no tool emitted the values they would plot. Computing them in
the frontend was the one option ruled out — it would put statistics above the
tool boundary.
"""

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

import econometrica.econ.efficiency  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from tests.econ.fixtures import make_stationary_ar1


def run(series: pd.Series, **params):
    tool = get_registry().get("acf")
    frame = pd.DataFrame({"r": series.to_numpy()})
    return tool.fn(frame, tool.params_model.model_validate({"column": "r", **params}))


def white_noise(n: int = 2000, seed: int = 1) -> pd.Series:
    return pd.Series(np.random.default_rng(seed).normal(size=n))


def test_an_ar1_autocorrelation_decays_geometrically():
    """The defining property: rho(k) = phi^k."""
    result = run(make_stationary_ar1(phi=0.7, n=4000, seed=3), nlags=5)

    values = result.series["acf"].y
    for lag, value in enumerate(values[:4], start=1):
        assert value == pytest.approx(0.7**lag, abs=0.06), f"lag {lag}"


def test_an_ar1_partial_autocorrelation_spikes_once_then_vanishes():
    """What distinguishes PACF from ACF, and the reason both are plotted."""
    result = run(make_stationary_ar1(phi=0.7, n=4000, seed=3), nlags=5)

    values = result.series["pacf"].y
    assert values[0] == pytest.approx(0.7, abs=0.06)
    for value in values[1:]:
        assert abs(value) < 0.06


def test_white_noise_passes_across_many_seeds():
    """The multiple-comparisons trap, and why the verdict is a binomial test.

    Each lag is its own test at alpha, so white noise crosses at least one
    band roughly 40% of the time at ten lags. A diagnostic that failed on any
    crossing would fail on genuinely random data two runs in five. Counting
    crossings against Binomial(nlags, alpha) is the honest verdict, and it
    has to hold across seeds rather than on a lucky one.
    """
    verdicts = [
        next(
            d for d in run(white_noise(seed=seed), nlags=10).diagnostics
            if d.name == "acf_significance"
        ).passed
        for seed in range(12)
    ]

    failures = verdicts.count(False)
    assert all(verdicts), f"white noise was called predictable on {failures} of 12 seeds"


def test_a_single_chance_crossing_is_not_called_predictable():
    result = run(white_noise(seed=1), nlags=10)

    diagnostic = next(d for d in result.diagnostics if d.name == "acf_significance")
    assert diagnostic.statistic >= 1, "this seed is chosen for having a crossing"
    assert diagnostic.passed is True
    assert diagnostic.p_value is not None and diagnostic.p_value > 0.05


def test_an_ar1_is_flagged_as_predictable():
    result = run(make_stationary_ar1(phi=0.7, n=2000, seed=3), nlags=10)

    diagnostic = next(d for d in result.diagnostics if d.name == "acf_significance")
    assert diagnostic.passed is False
    assert diagnostic.statistic > 0


def test_bartlett_bands_widen_with_lag():
    """Bartlett's formula accumulates the variance of earlier lags.

    A flat +/-1.96/sqrt(n) band is the common shortcut and understates
    significance at longer lags on an autocorrelated series.
    """
    result = run(make_stationary_ar1(phi=0.7, n=2000, seed=3), nlags=10)

    upper = result.series["acf_upper"].y
    assert upper[-1] > upper[0]
    assert all(later >= earlier for earlier, later in pairwise(upper))


def test_the_bands_are_symmetric_about_zero():
    """A stem chart mirrors them; asymmetry would be silently mis-drawn."""
    result = run(white_noise(), nlags=8)

    for upper, lower in zip(
        result.series["acf_upper"].y, result.series["acf_lower"].y, strict=True
    ):
        assert lower == pytest.approx(-upper)


def test_lag_zero_is_omitted():
    """It is exactly 1 by construction, and plotting it flattens every real lag."""
    result = run(white_noise(), nlags=6)

    assert result.series["acf"].x == [1, 2, 3, 4, 5, 6]
    assert len(result.series["acf"].y) == 6


def test_the_table_and_the_series_agree():
    result = run(white_noise(), nlags=5)

    table = result.tables["autocorrelations"]
    assert table.columns[0] == "lag"
    assert [row[0] for row in table.rows] == result.series["acf"].x
    assert [row[1] for row in table.rows] == pytest.approx(result.series["acf"].y)


def test_the_lag_count_is_reported():
    result = run(white_noise(), nlags=7)

    assert result.scalars["nlags"] == 7
    assert result.scalars["nobs"] == 2000


def test_the_default_lag_count_is_deterministic():
    """Reproducibility reaches the lag rule, not only the arithmetic."""
    first = run(white_noise())
    second = run(white_noise())

    assert first.scalars["nlags"] == second.scalars["nlags"]
    assert first.series["acf"].y == pytest.approx(second.series["acf"].y)


def test_more_lags_than_observations_is_refused():
    with pytest.raises(ValueError, match="lag"):
        run(white_noise(n=50), nlags=60)


def test_too_few_observations_are_refused():
    with pytest.raises(ValueError):
        run(white_noise(n=5), nlags=2)


def test_the_manifest_records_the_tool_and_its_version():
    result = run(white_noise(), nlags=5)

    assert result.manifest.tool == "acf"
    assert result.manifest.tool_version == result.version
    assert result.manifest.data_fingerprint
