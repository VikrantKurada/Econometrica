"""The chart spec union.

A Visualizer emits these as JSON from a closed vocabulary and never writes
drawing code — the same containment as the tool registry. So the union is
where a bad chart gets stopped, and the interesting tests are the refusals.
"""

import json

import pytest
from pydantic import ValidationError

from econometrica.charts.spec import (
    CHART_TYPES,
    MAX_SERIES,
    SCATTER_SERIES_CAP,
    ChartSpec,
    SeriesRef,
    parse_chart_spec,
    unresolved_references,
)
from econometrica.econ.types import Manifest, ResultSet, Series, Table


def result_set() -> ResultSet:
    return ResultSet(
        tool="acf",
        version="1.0.0",
        params={},
        scalars={"nobs": 500.0},
        series={
            "acf": Series(name="acf", x=[1, 2], y=[0.5, 0.2]),
            "acf_upper": Series(name="acf_upper", x=[1, 2], y=[0.09, 0.1]),
            "acf_lower": Series(name="acf_lower", x=[1, 2], y=[-0.09, -0.1]),
        },
        tables={"autocorrelations": Table(columns=["lag", "acf"], rows=[[1.0, 0.5]])},
        manifest=Manifest(data_fingerprint="abc", tool="acf", tool_version="1.0.0"),
    )


def spec(**overrides) -> dict:
    payload = {"type": "line", "title": "Conditional volatility", "series": [{"key": "acf"}]}
    payload.update(overrides)
    return payload


# --- the vocabulary ---------------------------------------------------------


def test_every_declared_type_round_trips():
    assert len(CHART_TYPES) >= 12
    for chart in _one_of_each():
        restored = parse_chart_spec(json.loads(chart.model_dump_json()))
        assert restored == chart


def test_an_unknown_type_is_rejected_and_the_error_lists_the_known_ones():
    with pytest.raises(ValidationError) as excinfo:
        parse_chart_spec({"type": "sankey", "title": "t"})

    message = str(excinfo.value)
    assert "line" in message and "heatmap" in message


def test_an_invented_field_is_rejected_rather_than_ignored():
    """Same reasoning as PlanStep: a silently dropped field misleads.

    "smoothing": true that vanishes leaves a reader believing the line was
    smoothed.
    """
    with pytest.raises(ValidationError, match="smoothing"):
        parse_chart_spec(spec(smoothing=True))


def test_a_title_is_required():
    with pytest.raises(ValidationError):
        parse_chart_spec({"type": "line", "series": [{"key": "acf"}]})


# --- the dual-axis ban ------------------------------------------------------


def test_no_chart_type_can_express_a_second_y_axis():
    """The rule survives someone adding a type later, not just today's set.

    A dual-axis chart's crossing point is an artifact of the two scalings and
    readers infer causation from it. Making it unrepresentable is stronger
    than discouraging it, so this asserts on the shape of the union itself.
    """
    forbidden = {"y2", "yaxis2", "y_axis_2", "secondary_y", "right_axis", "axis2"}

    for model in CHART_TYPES.values():
        overlap = forbidden & set(model.model_fields)
        assert overlap == set(), f"{model.__name__} declares a second axis: {overlap}"


def test_two_measures_are_expressed_as_stacked_panels():
    """The replacement for the overlay: one x-axis, two panels, no false crossing."""
    chart = parse_chart_spec(
        {
            "type": "panels",
            "title": "Price and fitted volatility",
            "panels": [
                {"title": "Price", "series": [{"key": "acf"}]},
                {"title": "Conditional volatility", "series": [{"key": "acf_upper"}]},
            ],
        }
    )

    assert len(chart.panels) == 2
    assert chart.shared_x is True


def test_a_single_panel_is_refused():
    """One panel is a line chart wearing a costume."""
    with pytest.raises(ValidationError):
        parse_chart_spec(
            {"type": "panels", "title": "t", "panels": [{"title": "a", "series": [{"key": "acf"}]}]}
        )


# --- palette limits, which are validated facts not preferences --------------


def test_more_series_than_the_palette_has_slots_is_rejected():
    with pytest.raises(ValidationError):
        parse_chart_spec(spec(series=[{"key": f"s{i}"} for i in range(MAX_SERIES + 1)]))


def test_a_scatter_caps_lower_than_the_other_forms():
    """Only the first three slots clear the all-pairs floors; scatter compares
    every pair at once, so it takes the lower cap."""
    assert SCATTER_SERIES_CAP < MAX_SERIES

    ok = parse_chart_spec(
        {
            "type": "scatter",
            "title": "Security market line",
            "x": {"key": "acf"},
            "y": {"key": "acf_upper"},
            "groups": [{"key": f"g{i}"} for i in range(SCATTER_SERIES_CAP)],
        }
    )
    assert len(ok.groups) == SCATTER_SERIES_CAP

    with pytest.raises(ValidationError, match=r"three"):
        parse_chart_spec(
            {
                "type": "scatter",
                "title": "t",
                "x": {"key": "acf"},
                "y": {"key": "acf_upper"},
                "groups": [{"key": f"g{i}"} for i in range(SCATTER_SERIES_CAP + 1)],
            }
        )


# --- heatmaps: the scale has to match the data's polarity -------------------


def test_a_correlation_heatmap_must_be_diverging():
    """A single-hue ramp over -1..1 hides the sign, which is the whole reading."""
    with pytest.raises(ValidationError, match="diverging"):
        parse_chart_spec(
            {
                "type": "heatmap",
                "title": "Correlations",
                "table": "autocorrelations",
                "scale": "sequential",
                "domain": [-1.0, 1.0],
            }
        )


def test_a_correlation_heatmap_is_accepted_as_diverging():
    chart = parse_chart_spec(
        {
            "type": "heatmap",
            "title": "Correlations",
            "table": "autocorrelations",
            "scale": "diverging",
            "domain": [-1.0, 1.0],
        }
    )
    assert chart.scale == "diverging"


def test_a_one_sided_heatmap_may_be_sequential():
    chart = parse_chart_spec(
        {
            "type": "heatmap",
            "title": "p-values",
            "table": "autocorrelations",
            "scale": "sequential",
            "domain": [0.0, 1.0],
        }
    )
    assert chart.scale == "sequential"


# --- binding to a result ----------------------------------------------------


def test_a_spec_naming_a_series_the_result_lacks_is_reported():
    """A chart of data that does not exist is an ungrounded number with a line
    through it."""
    chart = parse_chart_spec(spec(series=[{"key": "acf"}, {"key": "invented"}]))

    missing = unresolved_references(chart, result_set())

    assert missing == ["series 'invented'"]


def test_a_spec_that_binds_cleanly_reports_nothing():
    chart = parse_chart_spec(spec(series=[{"key": "acf"}, {"key": "acf_upper"}]))

    assert unresolved_references(chart, result_set()) == []


def test_a_missing_table_is_reported_too():
    chart = parse_chart_spec(
        {
            "type": "heatmap",
            "title": "t",
            "table": "nope",
            "scale": "sequential",
            "domain": [0.0, 1.0],
        }
    )

    assert unresolved_references(chart, result_set()) == ["table 'nope'"]


def test_panels_are_checked_panel_by_panel():
    chart = parse_chart_spec(
        {
            "type": "panels",
            "title": "t",
            "panels": [
                {"title": "a", "series": [{"key": "acf"}]},
                {"title": "b", "series": [{"key": "ghost"}]},
            ],
        }
    )

    assert unresolved_references(chart, result_set()) == ["series 'ghost'"]


def test_a_stat_tile_binds_to_a_scalar():
    chart = parse_chart_spec({"type": "stat_tile", "title": "Observations", "scalar": "nobs"})

    assert unresolved_references(chart, result_set()) == []
    assert unresolved_references(
        parse_chart_spec({"type": "stat_tile", "title": "t", "scalar": "absent"}), result_set()
    ) == ["scalar 'absent'"]


# --- helpers ----------------------------------------------------------------


def _one_of_each() -> list[ChartSpec]:
    payloads: list[dict] = [
        spec(),
        {"type": "band", "title": "Rolling beta", "center": {"key": "acf"},
         "lower": {"key": "acf_lower"}, "upper": {"key": "acf_upper"}},
        {"type": "stem", "title": "ACF", "series": {"key": "acf"},
         "upper": {"key": "acf_upper"}, "lower": {"key": "acf_lower"}},
        {"type": "panels", "title": "t", "panels": [
            {"title": "a", "series": [{"key": "acf"}]},
            {"title": "b", "series": [{"key": "acf_upper"}]}]},
        {"type": "scatter", "title": "t", "x": {"key": "acf"}, "y": {"key": "acf_upper"}},
        {"type": "bar", "title": "t", "series": [{"key": "acf"}]},
        {"type": "forest", "title": "t", "estimates": ["alpha", "beta"]},
        {"type": "heatmap", "title": "t", "table": "autocorrelations",
         "scale": "sequential", "domain": [0.0, 1.0]},
        {"type": "qq", "title": "t", "series": {"key": "acf"}},
        {"type": "histogram", "title": "t", "series": {"key": "acf"}},
        {"type": "area_stack", "title": "t", "series": [{"key": "acf"}]},
        {"type": "underwater", "title": "t", "series": {"key": "acf"}},
        {"type": "stat_tile", "title": "t", "scalar": "nobs"},
        {"type": "table", "title": "t", "table": "autocorrelations"},
    ]
    return [parse_chart_spec(payload) for payload in payloads]


def test_the_sample_covers_every_declared_type():
    """A type nobody exercises is a type nobody has checked."""
    covered = {chart.type for chart in _one_of_each()}

    assert covered == set(CHART_TYPES)


def test_a_series_ref_labels_itself_from_its_key_by_default():
    assert SeriesRef(key="acf").label == "acf"
    assert SeriesRef(key="acf", label="Autocorrelation").label == "Autocorrelation"
