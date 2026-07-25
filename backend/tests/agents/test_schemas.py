"""The contract between agents.

These types are the reason a malformed model reply cannot reach the
econometrics core. Every check here is a refusal the Planner would otherwise
have to be trusted to make.
"""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from econometrica.agents.schemas import (
    AgentOutputError,
    AnalysisPlan,
    DatasetSpec,
    PlanStep,
    ValidationVerdict,
    parse_agent_json,
)
from econometrica.econ.registry import get_registry


def dataset(**overrides: object) -> DatasetSpec:
    payload: dict[str, object] = {
        "tickers": ["BTC-USD"],
        "start": date(2020, 1, 1),
        "end": date(2024, 1, 1),
    }
    payload.update(overrides)
    return DatasetSpec(**payload)  # type: ignore[arg-type]


def test_the_tool_registry_is_populated_when_the_schemas_are_imported():
    """Registration is an import side-effect of the five family packages.

    Nothing in the running application imported them before Phase 4, so a live
    server's registry was empty. Validating a plan step against an empty
    registry would reject every real tool, so importing the schemas has to
    guarantee the registry is loaded.
    """
    names = {tool.name for tool in get_registry().all()}
    assert {"capm", "adf", "garch", "variance_ratio", "event_study"} <= names


def test_analysis_plan_round_trips_through_json():
    plan = AnalysisPlan(
        question="Does Bitcoin follow a random walk?",
        dataset=dataset(),
        steps=[
            PlanStep(id="s1", tool="adf", params={"column": "price"}),
            PlanStep(id="s2", tool="variance_ratio", params={}, depends_on=["s1"]),
        ],
        hypotheses=["BTC prices contain a unit root"],
    )

    restored = AnalysisPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert restored.steps[1].depends_on == ["s1"]


def test_plan_step_rejects_a_tool_that_is_not_registered():
    with pytest.raises(ValidationError, match="unknown tool"):
        PlanStep(id="s1", tool="regress_vibes", params={})


def test_plan_step_rejects_params_its_tool_would_refuse():
    # AdfParams declares min_obs >= 15; a model asking for 3 has misread it.
    with pytest.raises(ValidationError, match="min_obs") as excinfo:
        PlanStep(id="s1", tool="adf", params={"min_obs": 3})

    assert "adf" in str(excinfo.value)


def test_plan_step_rejects_a_parameter_the_tool_does_not_have():
    """Silently dropping an invented parameter is the dangerous failure.

    Pydantic ignores extras by default, so `confidence` would vanish and the
    user would believe a 99% level had been honoured.
    """
    with pytest.raises(ValidationError, match="confidence"):
        PlanStep(id="s1", tool="adf", params={"confidence": 0.99})


def test_a_plan_with_no_steps_is_rejected():
    """An empty plan is a parse failure wearing the costume of success."""
    with pytest.raises(ValidationError):
        AnalysisPlan(question="Anything?", dataset=dataset(), steps=[])


def test_plan_rejects_duplicate_step_ids():
    with pytest.raises(ValidationError, match="duplicate step id"):
        AnalysisPlan(
            question="q",
            dataset=dataset(),
            steps=[
                PlanStep(id="s1", tool="adf", params={}),
                PlanStep(id="s1", tool="kpss", params={}),
            ],
        )


def test_plan_rejects_a_dependency_on_a_step_that_does_not_exist():
    with pytest.raises(ValidationError, match="unknown step"):
        AnalysisPlan(
            question="q",
            dataset=dataset(),
            steps=[PlanStep(id="s1", tool="adf", params={}, depends_on=["s0"])],
        )


def test_plan_rejects_a_dependency_cycle():
    """A plan whose steps cannot be ordered is not a plan."""
    with pytest.raises(ValidationError, match="cycle"):
        AnalysisPlan(
            question="q",
            dataset=dataset(),
            steps=[
                PlanStep(id="s1", tool="adf", params={}, depends_on=["s2"]),
                PlanStep(id="s2", tool="kpss", params={}, depends_on=["s1"]),
            ],
        )


def test_ordered_steps_puts_dependencies_first():
    plan = AnalysisPlan(
        question="q",
        dataset=dataset(),
        steps=[
            PlanStep(id="last", tool="garch", params={}, depends_on=["first"]),
            PlanStep(id="first", tool="adf", params={}),
        ],
    )

    assert [step.id for step in plan.ordered_steps()] == ["first", "last"]


def test_dataset_spec_rejects_a_backwards_window():
    with pytest.raises(ValidationError, match="end"):
        dataset(start=date(2024, 1, 1), end=date(2020, 1, 1))


def test_log_diff_is_accepted_as_a_spelling_of_log_returns():
    """The prompt carries two vocabularies for one concept, so models mix them.

    `DatasetSpec.return_method` takes "log"; the tool-level `transform` in the
    same catalogue takes "log_diff". They mean the same thing for returns — a
    log difference *is* a log return — and a real local model reached for the
    tool spelling on its first attempt every time, burning a retry on a
    synonym. Recognising it is not leniency about meaning.
    """
    assert dataset(return_method="log_diff").return_method == "log"


def test_a_return_method_that_is_not_a_synonym_is_still_rejected():
    with pytest.raises(ValidationError):
        dataset(return_method="diff")  # a price difference, not a return


def test_dataset_spec_rejects_an_empty_ticker_list():
    with pytest.raises(ValidationError):
        dataset(tickers=[])


def test_a_rejection_must_carry_reasons():
    """A refusal a user cannot act on is worse than no refusal at all."""
    with pytest.raises(ValidationError, match="reason"):
        ValidationVerdict(approved=False, reasons=[])


def test_an_approval_needs_no_reasons():
    assert ValidationVerdict(approved=True).approved is True


def test_parse_agent_json_recovers_a_fenced_block():
    raw = 'Here is the plan:\n```json\n{"a": 1}\n```\nHope that helps!'
    assert parse_agent_json(raw) == {"a": 1}


def test_parse_agent_json_recovers_an_unfenced_object_from_prose():
    raw = 'Sure. {"a": 1, "b": [2, 3]} — let me know if you want changes.'
    assert parse_agent_json(raw) == {"a": 1, "b": [2, 3]}


def test_parse_agent_json_handles_a_bare_object():
    assert parse_agent_json(json.dumps({"a": 1})) == {"a": 1}


def test_parse_agent_json_keeps_the_raw_text_for_the_retry():
    """The retry has to show the model what it actually sent."""
    with pytest.raises(AgentOutputError) as excinfo:
        parse_agent_json("I'd rather not answer that.")

    assert excinfo.value.raw == "I'd rather not answer that."


def test_parse_agent_json_rejects_a_json_array():
    """Every agent contract in this phase is an object; a list is a misread."""
    with pytest.raises(AgentOutputError):
        parse_agent_json("[1, 2, 3]")
