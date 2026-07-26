"""Charts chosen from result shape, and edited by a model only afterwards."""

import json

import numpy as np
import pandas as pd
import pytest

from econometrica.agents.visualizer import Visualizer
from econometrica.charts.propose import propose_charts
from econometrica.charts.spec import unresolved_references
from econometrica.econ import load_tools
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, Manifest, ResultSet, Series
from econometrica.llm.fake import FakeProvider

load_tools()


def run_tool(name: str, frame: pd.DataFrame, **params):
    tool = get_registry().get(name)
    return tool.fn(frame, tool.params_model.model_validate(params))


def returns(n: int = 900, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    values = np.zeros(n)
    variance = 1e-4
    for i in range(1, n):
        variance = 1e-6 + 0.09 * values[i - 1] ** 2 + 0.90 * variance
        values[i] = rng.normal(0.0, np.sqrt(variance))
    return pd.DataFrame({"r": values, "p": 100 * np.exp(np.cumsum(values))})


def var_frame(n: int = 800, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    a, b = np.zeros(n), np.zeros(n)
    for i in range(1, n):
        a[i] = 0.5 * a[i - 1] + 0.2 * b[i - 1] + rng.normal()
        b[i] = 0.3 * b[i - 1] - 0.1 * a[i - 1] + rng.normal()
    return pd.DataFrame({"a": a, "b": b})


# --- the two named acceptance criteria --------------------------------------


def test_a_garch_result_proposes_a_conditional_volatility_chart():
    result = run_tool("garch", returns(), column="r")

    charts = propose_charts(result)
    panels = next(chart for chart in charts if chart.type == "panels")

    assert "conditional_volatility" in [
        ref.key for panel in panels.panels for ref in panel.series
    ]
    # Two panels sharing an x-axis, never two y-scales on one plot.
    assert len(panels.panels) == 2
    assert panels.shared_x is True


def test_a_var_result_proposes_an_impulse_response_grid():
    result = run_tool("irf", var_frame(), columns=["a", "b"], horizon=10)

    charts = propose_charts(result)
    grid = next(chart for chart in charts if chart.type == "panels")

    # One panel per impulse-response pair: 2 variables give 4.
    assert len(grid.panels) == 4
    assert {panel.title for panel in grid.panels} == {"a → a", "a → b", "b → a", "b → b"}


# --- the invariant that matters ---------------------------------------------


#: Tools whose results carry something a chart type can bind to.
DRAWABLE = [
    ("garch", {"column": "r"}),
    ("acf", {"column": "r", "nlags": 8}),
    ("drawdown", {"column": "p"}),
    ("realized_vol", {"column": "r"}),
    ("ewma_vol", {"column": "r"}),
    ("variance_ratio", {"column": "p", "transform": "log"}),
    ("hurst", {"column": "r"}),
    ("ljung_box", {"column": "r"}),
    ("historical_var", {"column": "r"}),
]

#: A pure hypothesis test reports a statistic and a p-value, and both live in
#: `diagnostics` — which no member of the chart union binds to. It proposes
#: nothing on purpose; the canvas renders diagnostics directly. Its scalars are
#: the sample size and the lag order, and tiles of those would be a canvas
#: leading with the least interesting numbers in the result.
NOT_DRAWABLE = [("adf", {"column": "p", "transform": "log"})]


@pytest.mark.parametrize(("tool", "params"), DRAWABLE + NOT_DRAWABLE)
def test_every_proposed_chart_binds_to_the_result_it_came_from(tool, params):
    """A chart of data that does not exist is an ungrounded number with a line
    drawn through it."""
    result = run_tool(tool, returns(), **params)

    for chart in propose_charts(result):
        assert unresolved_references(chart, result) == [], f"{tool} / {chart.type}"


@pytest.mark.parametrize(("tool", "params"), DRAWABLE)
def test_a_tool_that_can_be_drawn_is_drawn(tool, params):
    """The guard against a tool quietly falling out of every rule."""
    assert propose_charts(run_tool(tool, returns(), **params)), f"{tool} proposed nothing"


@pytest.mark.parametrize(("tool", "params"), NOT_DRAWABLE)
def test_a_hypothesis_test_proposes_nothing_rather_than_bookkeeping(tool, params):
    result = run_tool(tool, returns(), **params)

    assert propose_charts(result) == []
    # Not because the result is empty — because what it found is a diagnostic.
    assert result.diagnostics


def test_multivariate_results_bind_too():
    for name, params in (
        ("irf", {"columns": ["a", "b"], "horizon": 10}),
        ("fevd", {"columns": ["a", "b"], "horizon": 10}),
        ("var_model", {"columns": ["a", "b"]}),
    ):
        result = run_tool(name, var_frame(), **params)
        for chart in propose_charts(result):
            assert unresolved_references(chart, result) == [], f"{name} / {chart.type}"


# --- shape before name ------------------------------------------------------


def test_acf_and_pacf_each_get_their_own_stem():
    result = run_tool("acf", returns(), column="r", nlags=8)

    stems = [chart for chart in propose_charts(result) if chart.type == "stem"]

    assert [stem.series.key for stem in stems] == ["acf", "pacf"]


def test_a_tool_emitting_residuals_gets_a_qq_without_being_named():
    """The rule is keyed on shape, so a new tool inherits it."""
    invented = ResultSet(
        tool="a_tool_written_next_year",
        version="1.0.0",
        params={},
        series={"residuals": Series(name="residuals", x=[1, 2, 3], y=[0.1, -0.2, 0.05])},
        manifest=Manifest(data_fingerprint="x", tool="t", tool_version="1.0.0"),
    )

    assert [chart.type for chart in propose_charts(invented)] == ["qq"]


def test_a_result_with_nothing_plottable_still_gets_something_readable():
    bare = ResultSet(
        tool="scalars_only",
        version="1.0.0",
        params={},
        scalars={"nobs": 500.0, "statistic": 1.2},
        manifest=Manifest(data_fingerprint="x", tool="t", tool_version="1.0.0"),
    )

    charts = propose_charts(bare)

    assert charts
    assert all(chart.type == "stat_tile" for chart in charts)


def test_no_proposed_chart_ever_carries_a_second_axis():
    result = run_tool("garch", returns(), column="r")

    for chart in propose_charts(result):
        assert not any("y2" in field or "secondary" in field for field in chart.model_fields)


# --- the editorial pass -----------------------------------------------------


def test_charts_are_produced_with_no_provider_at_all():
    """The path most runs should take: selection needs no model."""
    result = run_tool("garch", returns(), column="r")

    assert len(propose_charts(result)) >= 1


async def test_a_model_may_reorder_and_retitle():
    result = run_tool("garch", returns(), column="r")
    proposed = propose_charts(result)
    assert len(proposed) >= 2

    provider = FakeProvider(
        responses=[
            json.dumps({"charts": [{"index": 1}, {"index": 0, "title": "Volatility clustering"}]})
        ]
    )

    charts, _ = await Visualizer(provider, "fake-1").curate(result, "Is volatility persistent?")

    assert [chart.type for chart in charts] == [proposed[1].type, proposed[0].type]
    assert charts[1].title == "Volatility clustering"


async def test_a_model_cannot_name_a_chart_that_is_not_offered():
    result = run_tool("garch", returns(), column="r")
    valid = json.dumps({"charts": [{"index": 0}]})
    provider = FakeProvider(responses=[json.dumps({"charts": [{"index": 99}]}), valid])

    charts, selection = await Visualizer(provider, "fake-1").curate(result)

    assert selection.attempts == 2
    assert "99" in provider.calls[1].messages[-1].content
    assert len(charts) == 1


async def test_the_same_chart_twice_is_rejected():
    result = run_tool("garch", returns(), column="r")
    valid = json.dumps({"charts": [{"index": 0}]})
    provider = FakeProvider(
        responses=[json.dumps({"charts": [{"index": 0}, {"index": 0}]}), valid]
    )

    charts, selection = await Visualizer(provider, "fake-1").curate(result)

    assert selection.attempts == 2
    assert len(charts) == 1


async def test_the_offered_charts_reach_the_model():
    result = run_tool("garch", returns(), column="r")
    provider = FakeProvider(responses=[json.dumps({"charts": [{"index": 0}]})])

    await Visualizer(provider, "fake-1").curate(result, "Is volatility persistent?")

    prompt = "\n".join(message.content for message in provider.calls[0].messages)
    assert "Is volatility persistent?" in prompt
    assert "[panels]" in prompt


def test_bookkeeping_scalars_do_not_become_stat_tiles():
    """An `adf` result's finding is its statistic and p-value, and both live in
    `diagnostics`. Its *scalars* are the observation count and the lag order —
    facts about the fit, not answers to anyone's question. Leading a canvas
    with "Nobs: 5,080" makes the analysis look like it missed the point.
    """
    adf_shaped = ResultSet(
        tool="adf",
        version="1.0.0",
        params={},
        scalars={"nobs": 482.0, "lags_used": 17.0},
        diagnostics=[
            Diagnostic(name="adf", statistic=-0.726, p_value=0.84, passed=False)
        ],
        manifest=Manifest(data_fingerprint="x", tool="adf", tool_version="1.0.0"),
    )

    assert propose_charts(adf_shaped) == []


def test_an_informative_scalar_beside_a_bookkeeping_one_still_gets_a_tile():
    mixed = ResultSet(
        tool="whatever",
        version="1.0.0",
        params={},
        scalars={"nobs": 500.0, "sharpe_ratio": 0.84},
        manifest=Manifest(data_fingerprint="x", tool="t", tool_version="1.0.0"),
    )

    charts = propose_charts(mixed)

    assert [chart.scalar for chart in charts] == ["sharpe_ratio"]
