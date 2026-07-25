"""The numeric grounding gate.

Every number in narrator prose must trace to a computed one. This is the
mechanical anti-hallucination check the design calls the single most important
safeguard in the system, and it is only worth having if it is strict about
fabrication *and* quiet about the ordinary ways people write numbers — a gate
that blocks "significant at the 5% level" gets switched off within a day.
"""

from econometrica.agents.grounding import allowed_values, check_grounding
from econometrica.econ.types import Diagnostic, Estimate, Manifest, ResultSet

ALLOWED = {1.2977, 0.83, 0.0213, 1250.0, 12.5}


def grounded(prose: str, allowed: set[float] | None = None) -> bool:
    return check_grounding(prose, allowed if allowed is not None else ALLOWED).grounded


# --- the point of the whole thing -------------------------------------------


def test_a_fabricated_statistic_is_blocked():
    assert grounded("The beta is 1.47.") is False


def test_a_computed_value_passes():
    assert grounded("The beta is 1.2977.") is True


def test_the_report_names_the_number_and_its_sentence():
    """The revision prompt has to be specific or the retry learns nothing."""
    report = check_grounding("Beta was 1.2977. R-squared reached 0.91.", ALLOWED)

    assert report.grounded is False
    [issue] = report.issues
    assert issue.value == 0.91
    assert "R-squared" in issue.sentence


# --- the ordinary ways people write numbers ---------------------------------


def test_a_correctly_rounded_value_passes():
    """Precision comes from the citation: 1.30 is 1.2977 to two places."""
    assert grounded("The beta is 1.30.") is True
    assert grounded("The beta is 1.3.") is True


def test_rounding_to_a_different_number_still_fails():
    assert grounded("The beta is 1.31.") is False


def test_a_decimal_restated_as_a_percentage_passes():
    assert grounded("The model explains 83% of the variance.") is True
    assert grounded("The p-value is 2.13%.") is True


def test_a_percentage_that_matches_nothing_still_fails():
    assert grounded("The model explains 91% of the variance.") is False


def test_thousands_separators_are_read_as_one_number():
    assert grounded("The sample holds 1,250 observations.") is True


def test_a_negative_sign_is_part_of_the_number():
    assert grounded("Alpha was -1.2977.", {-1.2977}) is True
    assert grounded("Alpha was -1.2977.", {1.2977}) is False


# --- exemptions, each of which must be earned -------------------------------


def test_a_year_in_a_date_context_is_exempt():
    assert grounded("Volatility spiked during the 2008 crisis.") is True
    assert grounded("The window runs from 2015 to 2024.") is True


def test_a_four_digit_number_presented_as_a_result_is_not_exempt():
    """"in 2008" is a date. "the statistic is 2008" is a claim."""
    assert grounded("The test statistic is 2008.") is False


def test_a_markdown_list_marker_is_not_a_claim():
    assert grounded("Findings:\n1. Beta is 1.2977.\n2. Fit is 83%.") is True


def test_a_real_figure_opening_a_line_is_still_checked():
    """The list exemption must not cover the numbers it exists beside."""
    assert grounded("Findings:\n1.4700 was the estimate.") is False
    assert grounded("Findings:\n1.2977 was the estimate.") is True


def test_an_artifact_reference_is_exempt():
    assert grounded("Beta is 1.2977, shown in figure 2.") is True
    assert grounded("Step 3 produced 0.83.") is True


def test_conventional_significance_levels_are_exempt_in_context():
    """Blocking "at the 5% level" would get the gate switched off by lunchtime."""
    assert grounded("Beta is 1.2977, significant at the 5% level.") is True
    assert grounded("We reject at the 1% significance level.") is True


def test_a_conventional_looking_number_outside_that_context_is_not_exempt():
    assert grounded("Returns rose 5% over the window.") is False


def test_model_orders_written_in_the_name_are_exempt():
    """GARCH(1,1) names the model; it is not a claim about the data."""
    assert grounded("A GARCH(1,1) fit gives persistence of 0.83.") is True


def test_prose_with_no_numbers_is_grounded():
    report = check_grounding("The series appears to wander without direction.", ALLOWED)
    assert report.grounded is True
    assert report.checked == 0


# --- what counts as computed ------------------------------------------------


def result_set() -> ResultSet:
    return ResultSet(
        tool="garch",
        version="1.0.0",
        params={"p": 1, "q": 1, "column": "r"},
        estimates=[Estimate(name="beta", value=1.2977, p_value=0.0213)],
        diagnostics=[Diagnostic(name="arch_lm", statistic=42.5, p_value=0.001)],
        scalars={"r_squared": 0.83, "nobs": 1250},
        manifest=Manifest(data_fingerprint="abc", tool="garch", tool_version="1.0.0"),
    )


def test_allowed_values_covers_estimates_diagnostics_and_scalars():
    values = allowed_values([result_set()])

    assert {1.2977, 0.0213, 42.5, 0.001, 0.83, 1250.0} <= values


def test_allowed_values_includes_the_parameters_that_produced_the_result():
    """A narrator may say which model was fitted, and orders live in params.

    `ResultSet.all_numeric_values()` covers only outputs, so without this a
    sentence naming the lag order it actually ran is blocked as fabricated.
    """
    assert {1.0} <= allowed_values([result_set()])


def test_a_sample_size_reported_as_a_scalar_is_citable():
    assert grounded("Estimated on 1250 observations.", allowed_values([result_set()]))
