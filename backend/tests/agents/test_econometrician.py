"""The Econometrician runs a plan, and refuses the parts it must not run."""

import pandas as pd

import econometrica.econ.multivariate  # registration side-effects
import econometrica.econ.volatility  # noqa: F401  — registration side-effects
from econometrica.agents.econometrician import Econometrician
from econometrica.agents.schemas import AnalysisPlan, DatasetSpec, PlanStep
from tests.econ.fixtures import make_garch_series, make_random_walk, make_stationary_ar1

N = 1200


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "clustered": make_garch_series(
                omega=1e-6, alpha=0.09, beta=0.90, n=N, seed=3
            ).to_numpy(),
            "quiet": make_stationary_ar1(phi=0.5, n=N, seed=3).to_numpy(),
            "walk": make_random_walk(n=N, seed=5).to_numpy(),
        }
    )


def plan(*steps: PlanStep) -> AnalysisPlan:
    return AnalysisPlan(
        question="q",
        dataset=DatasetSpec(tickers=["AAA"], start="2020-01-01", end="2024-01-01"),
        steps=list(steps),
    )


async def test_an_allowed_step_runs_and_produces_a_resultset():
    report = await Econometrician().run(
        plan(PlanStep(id="s1", tool="adf", params={"column": "walk"})), frame()
    )

    outcome = report.outcome("s1")
    assert outcome.status == "ran"
    assert outcome.result is not None
    assert outcome.result.tool == "adf"


async def test_a_gated_step_is_refused_and_never_runs():
    """The whole point: the tool is not invoked, so no number is produced."""
    report = await Econometrician().run(
        plan(PlanStep(id="s1", tool="garch", params={"column": "quiet"})), frame()
    )

    outcome = report.outcome("s1")
    assert outcome.status == "refused"
    assert outcome.result is None
    assert "estimates noise" in outcome.refusals[0].detail


async def test_the_same_tool_runs_when_its_gate_is_satisfied():
    report = await Econometrician().run(
        plan(PlanStep(id="s1", tool="garch", params={"column": "clustered"})), frame()
    )

    assert report.outcome("s1").status == "ran"


async def test_steps_run_in_dependency_order():
    report = await Econometrician().run(
        plan(
            PlanStep(id="second", tool="kpss", params={"column": "walk"}, depends_on=["first"]),
            PlanStep(id="first", tool="adf", params={"column": "walk"}),
        ),
        frame(),
    )

    assert [outcome.step_id for outcome in report.outcomes] == ["first", "second"]


async def test_a_step_depending_on_a_refused_step_is_skipped():
    """Running it would answer a question the refused step was meant to settle."""
    report = await Econometrician().run(
        plan(
            PlanStep(id="fit", tool="garch", params={"column": "quiet"}),
            PlanStep(id="after", tool="adf", params={"column": "walk"}, depends_on=["fit"]),
        ),
        frame(),
    )

    assert report.outcome("fit").status == "refused"
    assert report.outcome("after").status == "skipped"
    assert "fit" in report.outcome("after").error


async def test_a_tool_that_raises_is_recorded_rather_than_propagated():
    """One bad step must not lose the results of the good ones."""
    report = await Econometrician().run(
        plan(
            PlanStep(id="ok", tool="adf", params={"column": "walk"}),
            PlanStep(id="bad", tool="adf", params={"column": "walk", "min_obs": 99_999}),
        ),
        frame(),
    )

    assert report.outcome("ok").status == "ran"
    assert report.outcome("bad").status == "failed"
    assert report.outcome("bad").error


async def test_a_step_naming_a_column_the_data_lacks_fails_clearly():
    report = await Econometrician().run(
        plan(PlanStep(id="s1", tool="garch", params={"column": "absent"})), frame()
    )

    outcome = report.outcome("s1")
    assert outcome.status == "failed"
    assert "absent" in outcome.error


async def test_an_unjudged_gate_lets_the_step_run_and_says_so():
    """Not judged is not failed — and not silently passed either."""
    short = pd.DataFrame({"clustered": [0.01, -0.02, 0.015, 0.0, -0.01]})

    report = await Econometrician().run(
        plan(PlanStep(id="s1", tool="garch", params={"column": "clustered"})), short
    )

    outcome = report.outcome("s1")
    assert outcome.status != "refused"
    assert any(not verdict.judged for verdict in outcome.verdicts)
    assert report.unjudged


async def test_the_report_collects_results_by_step_id():
    report = await Econometrician().run(
        plan(
            PlanStep(id="a", tool="adf", params={"column": "walk"}),
            PlanStep(id="b", tool="kpss", params={"column": "walk"}),
        ),
        frame(),
    )

    assert set(report.results) == {"a", "b"}
    assert report.refusals == []
