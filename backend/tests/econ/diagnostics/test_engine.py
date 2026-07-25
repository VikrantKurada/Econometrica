"""Known-answer tests for the deterministic diagnostics engine.

The engine feeds the Phase 4 Validator agent FACTS about a fitted model rather
than asking an LLM to infer statistical properties. Every check below is
therefore tested on data whose true properties are known by construction.
"""

import numpy as np
import pandas as pd
import pytest

from econometrica.econ.diagnostics import ALL_CHECKS, run_diagnostics
from econometrica.econ.types import Diagnostic


def _named(diagnostics: list[Diagnostic], name: str) -> Diagnostic:
    match = [d for d in diagnostics if d.name == name]
    assert match, f"no diagnostic named {name!r} in {[d.name for d in diagnostics]}"
    return match[0]


def _white_noise(n: int = 800, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-02", periods=n)
    return pd.Series(rng.normal(0, 1, n), index=idx)


def _exog_for(resid: pd.Series, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {"const": 1.0, "x": rng.normal(0, 1, len(resid))}, index=resid.index
    )


# --- normality --------------------------------------------------------------


def test_jarque_bera_passes_on_normal_residuals():
    diags = run_diagnostics(_white_noise(), checks=["jarque_bera"])
    jb = _named(diags, "jarque_bera")
    assert jb.passed is True
    assert jb.p_value is not None and jb.p_value >= 0.05


def test_jarque_bera_rejects_fat_tailed_residuals():
    """Power check: t(5) is decisively non-normal at this sample size."""
    rng = np.random.default_rng(11)
    resid = pd.Series(rng.standard_t(5, 4000))
    jb = _named(run_diagnostics(resid, checks=["jarque_bera"]), "jarque_bera")
    assert jb.passed is False
    assert jb.p_value is not None and jb.p_value < 0.01


# --- heteroskedasticity -----------------------------------------------------


def test_breusch_pagan_and_white_pass_on_homoskedastic_residuals():
    resid = _white_noise()
    exog = _exog_for(resid)
    diags = run_diagnostics(resid, exog, checks=["breusch_pagan", "white"])
    assert _named(diags, "breusch_pagan").passed is True
    assert _named(diags, "white").passed is True


def test_breusch_pagan_and_white_fail_on_monotone_heteroskedasticity():
    """Residual scale rising with a positive regressor — both tests must catch it."""
    rng = np.random.default_rng(3)
    n = 800
    x = rng.uniform(1.0, 5.0, n)
    resid = pd.Series(rng.normal(0, 1, n) * x)
    exog = pd.DataFrame({"const": 1.0, "x": x}, index=resid.index)

    diags = run_diagnostics(resid, exog, checks=["breusch_pagan", "white"])

    bp = _named(diags, "breusch_pagan")
    assert bp.passed is False
    assert bp.p_value is not None and bp.p_value < 0.01
    assert _named(diags, "white").passed is False


def test_white_catches_symmetric_heteroskedasticity_that_breusch_pagan_misses():
    """The reason both tests ship, asserted rather than assumed.

    Here the variance depends on |x|, so it is symmetric in x. Breusch-Pagan
    regresses squared residuals *linearly* on the regressors and is structurally
    blind to this; White includes squares and cross products and sees it.
    """
    rng = np.random.default_rng(3)
    n = 800
    x = rng.normal(0, 1, n)
    resid = pd.Series(rng.normal(0, 1, n) * (0.5 + 2.0 * np.abs(x)))
    exog = pd.DataFrame({"const": 1.0, "x": x}, index=resid.index)

    diags = run_diagnostics(resid, exog, checks=["breusch_pagan", "white"])

    assert _named(diags, "breusch_pagan").passed is True
    white = _named(diags, "white")
    assert white.passed is False
    assert white.p_value is not None and white.p_value < 0.01


def test_heteroskedasticity_checks_require_exog_when_explicitly_requested():
    with pytest.raises(ValueError, match=r"breusch_pagan.*requires"):
        run_diagnostics(_white_noise(), checks=["breusch_pagan"])


def test_checks_none_without_exog_runs_only_the_applicable_subset():
    diags = run_diagnostics(_white_noise())
    names = {d.name for d in diags}
    assert "jarque_bera" in names
    assert "ljung_box" in names
    assert "breusch_pagan" not in names
    assert "white" not in names
    assert not any(n.startswith("vif") for n in names)


def test_checks_none_with_exog_runs_the_full_battery():
    resid = _white_noise()
    exog = _exog_for(resid)
    exog["x2"] = np.random.default_rng(9).normal(0, 1, len(resid))

    names = {d.name for d in run_diagnostics(resid, exog)}

    assert "breusch_pagan" in names
    assert "white" in names
    assert any(n.startswith("vif") for n in names)


# --- autocorrelation --------------------------------------------------------


def test_ljung_box_and_durbin_watson_pass_on_white_noise():
    diags = run_diagnostics(_white_noise(), checks=["ljung_box", "durbin_watson"])
    assert _named(diags, "ljung_box").passed is True
    dw = _named(diags, "durbin_watson")
    assert dw.passed is True
    assert 1.5 <= dw.statistic <= 2.5
    assert dw.p_value is None


def test_ljung_box_and_durbin_watson_fail_on_autocorrelated_residuals():
    from tests.econ.fixtures import make_stationary_ar1

    resid = make_stationary_ar1(phi=0.7, n=800, seed=4)
    diags = run_diagnostics(resid, checks=["ljung_box", "durbin_watson"])

    lb = _named(diags, "ljung_box")
    assert lb.passed is False
    assert lb.p_value is not None and lb.p_value < 1e-6

    dw = _named(diags, "durbin_watson")
    assert dw.passed is False
    assert dw.statistic < 1.5


# --- ARCH effects -----------------------------------------------------------


def test_arch_lm_passes_on_iid_and_fails_on_garch_residuals():
    from tests.econ.fixtures import make_garch_series

    assert _named(run_diagnostics(_white_noise(), checks=["arch_lm"]), "arch_lm").passed is True

    garch = make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=3000, seed=3)
    arch = _named(run_diagnostics(garch, checks=["arch_lm"]), "arch_lm")
    assert arch.passed is False
    assert arch.p_value is not None and arch.p_value < 0.01


# --- multicollinearity ------------------------------------------------------


def test_vif_flags_collinear_regressors():
    rng = np.random.default_rng(5)
    n = 500
    x1 = rng.normal(0, 1, n)
    exog = pd.DataFrame(
        {"const": 1.0, "x1": x1, "x2": x1 + rng.normal(0, 0.01, n)}
    )
    resid = pd.Series(rng.normal(0, 1, n))

    diags = run_diagnostics(resid, exog, checks=["vif"])

    assert _named(diags, "vif_x1").statistic > 10
    assert _named(diags, "vif_x1").passed is False
    assert _named(diags, "vif_x2").passed is False


def test_vif_passes_for_independent_regressors():
    rng = np.random.default_rng(6)
    n = 500
    exog = pd.DataFrame(
        {"const": 1.0, "x1": rng.normal(0, 1, n), "x2": rng.normal(0, 1, n)}
    )
    diags = run_diagnostics(pd.Series(rng.normal(0, 1, n)), exog, checks=["vif"])
    assert _named(diags, "vif_x1").passed is True
    assert _named(diags, "vif_x2").passed is True


def test_vif_needs_at_least_two_non_constant_regressors():
    resid = _white_noise(200)
    exog = pd.DataFrame({"const": 1.0, "x": np.arange(200.0)}, index=resid.index)
    with pytest.raises(ValueError, match=r"vif.*at least two"):
        run_diagnostics(resid, exog, checks=["vif"])


# --- stability and structural breaks ---------------------------------------


def test_stable_residuals_pass_cusum_and_structural_break():
    diags = run_diagnostics(_white_noise(), checks=["cusum", "structural_break"])
    assert _named(diags, "cusum").passed is True
    assert _named(diags, "structural_break").passed is True


def test_injected_mean_shift_is_detected_and_located():
    """A break at the midpoint must be found, and found in the right place."""
    rng = np.random.default_rng(7)
    n = 600
    resid = pd.Series(
        np.concatenate([rng.normal(0, 1, n // 2), rng.normal(4, 1, n // 2)])
    )

    diags = run_diagnostics(resid, checks=["cusum", "structural_break"])

    brk = _named(diags, "structural_break")
    assert brk.passed is False
    assert brk.statistic == pytest.approx(0.5, abs=0.05)
    assert brk.critical_values["break_index"] == pytest.approx(300, abs=30)
    assert brk.critical_values["effect_size"] > 1.0

    assert _named(diags, "cusum").passed is False


# --- engine contract --------------------------------------------------------


def test_engine_is_deterministic():
    resid = _white_noise()
    exog = _exog_for(resid)
    first = run_diagnostics(resid, exog)
    second = run_diagnostics(resid, exog)
    assert [d.model_dump(exclude={"interpretation"}) for d in first] == [
        d.model_dump(exclude={"interpretation"}) for d in second
    ]


def test_every_diagnostic_states_its_hypothesis():
    resid = _white_noise()
    exog = _exog_for(resid)
    exog["x2"] = np.random.default_rng(8).normal(0, 1, len(resid))
    for diag in run_diagnostics(resid, exog):
        assert diag.interpretation, f"{diag.name} has no interpretation"
        assert "passed" in diag.interpretation.lower(), (
            f"{diag.name} does not say what passed means"
        )


def test_unknown_check_name_raises():
    with pytest.raises(ValueError, match="unknown diagnostic check"):
        run_diagnostics(_white_noise(), checks=["nonsense"])


def test_all_checks_is_the_documented_battery():
    assert ALL_CHECKS == (
        "jarque_bera",
        "breusch_pagan",
        "white",
        "durbin_watson",
        "ljung_box",
        "arch_lm",
        "vif",
        "cusum",
        "structural_break",
    )


def test_alpha_is_honoured():
    """A check that passes at 1% can fail at 20% — the threshold is a parameter."""
    from tests.econ.fixtures import make_stationary_ar1

    resid = make_stationary_ar1(phi=0.06, n=1500, seed=12)
    strict = _named(run_diagnostics(resid, alpha=0.001, checks=["ljung_box"]), "ljung_box")
    loose = _named(run_diagnostics(resid, alpha=0.5, checks=["ljung_box"]), "ljung_box")
    assert strict.passed is True
    assert loose.passed is False


def test_too_few_observations_raises():
    with pytest.raises(ValueError, match="at least"):
        run_diagnostics(pd.Series([1.0, 2.0, 3.0]))
