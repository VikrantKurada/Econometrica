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


# --- step citations ----------------------------------------------------------
#
# The gate read the `3` in `(s3)` as a claim about data, so real narrations were
# withheld over their own citations — in a format this project's own prompts
# produce, since `narrator._render` labels every result block `## s3 — tool`.
#
# The exemption is keyed on the *plan's actual step ids*, not on the letter `s`.
# A citation of a step that exists is bookkeeping; `s7` where no s7 was planned
# is not a citation of anything, and stays checked.

STEPS = frozenset({"s1", "s2", "s3", "s10"})


def cited(prose: str, allowed: set[float] | None = None) -> bool:
    return check_grounding(
        prose, allowed if allowed is not None else ALLOWED, step_ids=STEPS
    ).grounded


def test_a_parenthesised_step_citation_is_exempt():
    assert cited("The beta is 1.2977 (s3).") is True


def test_several_step_citations_in_one_reference_are_exempt():
    """`(s1, s3)` used to block on the 3 while the 1 passed by coincidence —
    it happened to round to a real computed 1.2977 at zero decimals. The gate's
    behaviour here was arbitrary, not merely noisy."""
    assert cited("Both agree (s1, s3).") is True


def test_a_two_digit_step_id_is_exempt():
    assert cited("The tenth step disagrees (s10).") is True


def test_a_step_citation_outside_parentheses_is_exempt():
    """`narrator._render` labels blocks `## s3 — tool`, so the model refers to
    them in running prose too, not only in trailing parentheses."""
    assert cited("Step s3 fitted the model and s2 checked its residuals.") is True


def test_an_uppercase_step_citation_is_exempt():
    assert cited("The beta is 1.2977 (S3).") is True


# --- and the ways it must NOT widen ------------------------------------------


def test_a_step_that_was_never_planned_is_still_checked():
    """This is what keys the exemption to the plan rather than to the letter.
    `s7` cites nothing, so its 7 is an unexplained number like any other."""
    assert cited("The result came from (s7).") is False


def test_without_the_plan_no_step_exemption_applies():
    """`check_grounding` is called elsewhere and in tests without step ids. The
    exemption must be something a caller opts into by supplying real ids, never
    a hole that opens by default."""
    assert grounded("The beta is 1.2977 (s3).") is False


def test_a_decimal_after_a_step_prefix_is_still_checked():
    """`s1.47` is not a step id — the exemption covers whole integers only, so
    it cannot be used to smuggle a fabricated figure past the gate."""
    assert cited("The estimate is s1.47 here.") is False


def test_a_number_before_a_letter_s_is_not_a_citation():
    """`3s` is a duration or a unit, not `s3`. Order matters."""
    assert cited("It converged in 3s.") is False


def test_the_fabricated_digit_case_still_fails():
    """The gate earned its keep by catching a model writing -15.066 where the
    computed statistic was -15.065457. Non-negotiable, and it lives here beside
    the exemption so nobody widens the tolerance to make a future case pass."""
    assert cited("The ADF statistic is -15.066 (s1).", {-15.065457}) is False
    assert cited("The ADF statistic is -15.065 (s1).", {-15.065457}) is True


def test_a_real_narration_citing_three_steps_publishes():
    """The case that motivated the fix: correct figures, correct citations, and
    the whole reply withheld over the citations."""
    prose = (
        "The market loading is 1.2977 (s1), so the asset moves more than the "
        "index. The model explains 0.83 of variance (s2), and the residuals "
        "show no remaining structure (s3)."
    )

    report = check_grounding(prose, ALLOWED, step_ids=STEPS)

    assert report.grounded is True
    assert report.issues == []


# --- year ranges -------------------------------------------------------------
#
# Found by running a real narration rather than by reading the code. A live
# model titled its answer "Volatility Persistence in BTC-USD Log Returns
# (2020-2024)" and the gate withheld the whole thing over the two years in its
# own heading: `_is_year` exempts "in 2008" by looking at the preceding word,
# and in a range the preceding word is whatever the title happened to say.
#
# The window is in the plan and is rendered into the prompt, so a model
# restating it is doing exactly what it was asked to do.


def test_a_year_range_with_an_en_dash_is_exempt():
    # U+2013, by code point rather than as a literal — it is indistinguishable
    # from a hyphen on sight, and this test is precisely about telling them
    # apart. It is what the live model wrote.
    en_dash = chr(0x2013)
    assert grounded(f"Volatility persistence in BTC-USD (2020{en_dash}2024) was high.") is True


def test_a_year_range_with_a_hyphen_is_exempt():
    """The hyphen case is not the same code path: `-2024` parses with a sign,
    so it arrives looking like a negative number rather than a year."""
    assert grounded("Volatility persistence in BTC-USD (2020-2024) was high.") is True


def test_a_year_range_written_out_is_still_exempt():
    assert grounded("The window ran from 2020 to 2024.") is True


def test_a_four_digit_result_beside_a_dash_is_not_exempt():
    """The exemption needs a year on *both* sides. A lone four-digit figure
    presented as a finding stays checked, dash or no dash."""
    assert grounded("The statistic is 2020, well above the threshold.") is False
    assert grounded("The estimate is 2020 - 15 basis points.") is False


def test_a_range_of_numbers_that_are_not_years_is_not_exempt():
    assert grounded("Values ranged 1200-1300 across the sample.") is False


def test_a_range_outside_the_year_window_is_not_exempt():
    assert grounded("Counts ranged 2500-2600 over the period.") is False
