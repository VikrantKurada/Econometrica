import pytest

from econometrica.econ.types import Diagnostic, Estimate, Manifest, ResultSet


def test_estimate_derives_significance_from_p_value():
    est = Estimate(name="beta", value=1.2, std_error=0.1, t_stat=12.0, p_value=0.001)
    assert est.is_significant(alpha=0.05) is True
    assert est.is_significant(alpha=0.0005) is False


def test_estimate_without_p_value_is_not_significant():
    est = Estimate(name="beta", value=1.2)
    assert est.is_significant() is False


def test_resultset_exposes_estimates_by_name():
    rs = ResultSet(
        tool="capm",
        version="1.0.0",
        params={},
        estimates=[Estimate(name="alpha", value=0.001), Estimate(name="beta", value=1.1)],
        manifest=Manifest(data_fingerprint="abc", tool="capm", tool_version="1.0.0"),
    )
    assert rs.estimate("beta").value == pytest.approx(1.1)
    assert rs.estimate("missing") is None


def test_resultset_collects_all_numeric_values_for_grounding_check():
    """The numeric grounding gate needs every number a narrator may cite."""
    rs = ResultSet(
        tool="capm",
        version="1.0.0",
        params={},
        estimates=[Estimate(name="beta", value=1.1, p_value=0.02)],
        scalars={"r_squared": 0.83},
        manifest=Manifest(data_fingerprint="abc", tool="capm", tool_version="1.0.0"),
    )
    values = rs.all_numeric_values()
    assert 1.1 in values
    assert 0.02 in values
    assert 0.83 in values


def test_diagnostic_passed_is_explicit_not_inferred():
    diag = Diagnostic(name="jarque_bera", statistic=3.1, p_value=0.21, passed=True)
    assert diag.passed is True
