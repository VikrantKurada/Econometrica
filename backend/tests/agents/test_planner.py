"""The Planner turns a question into a plan the core can actually run."""

import json

import pytest

from econometrica.agents.base import AgentAttemptsExhaustedError
from econometrica.agents.catalogue import render_tool_catalogue
from econometrica.agents.planner import Planner
from econometrica.econ.registry import get_registry
from econometrica.llm.fake import FakeProvider

QUESTION = "Does Bitcoin follow a random walk?"

PLAN = {
    "question": QUESTION,
    "dataset": {"tickers": ["BTC-USD"], "start": "2020-01-01", "end": "2024-01-01"},
    "steps": [
        {"id": "s1", "tool": "adf", "params": {"column": "price"}, "rationale": "unit root"},
        {"id": "s2", "tool": "variance_ratio", "params": {}, "depends_on": ["s1"]},
    ],
    "hypotheses": ["BTC prices contain a unit root"],
}


def system_message(provider: FakeProvider, call: int = 0) -> str:
    return provider.calls[call].messages[0].content


def user_message(provider: FakeProvider, call: int = 0) -> str:
    return provider.calls[call].messages[1].content


async def test_a_scripted_reply_becomes_an_analysis_plan():
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    result = await Planner(provider, "fake-1").plan(QUESTION)

    assert result.output.question == QUESTION
    assert [step.tool for step in result.output.ordered_steps()] == ["adf", "variance_ratio"]
    assert result.output.dataset.tickers == ["BTC-USD"]


async def test_the_question_reaches_the_model():
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    await Planner(provider, "fake-1").plan(QUESTION)

    assert QUESTION in user_message(provider)


async def test_the_tool_catalogue_reaches_the_model():
    """A planner that cannot see the tools invents them."""
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    await Planner(provider, "fake-1").plan(QUESTION)

    prompt = system_message(provider)
    assert "adf" in prompt
    assert "garch" in prompt
    # Parameter names too, or the model can name a tool but not configure it.
    assert "min_obs" in prompt


async def test_the_catalogue_covers_every_registered_tool():
    catalogue = render_tool_catalogue()

    for tool in get_registry().all():
        assert tool.name in catalogue


async def test_the_column_naming_convention_reaches_the_model():
    """Without it a real model plans against the tools' default column names.

    The data is assembled after planning, from the tickers the plan requests,
    so the columns are `BTC-USD` and `BTC-USD_return` — never `price` or
    `return`. A live probe produced exactly that mistake, and every step of
    the plan would have failed at execution.
    """
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    await Planner(provider, "fake-1").plan(QUESTION)

    prompt = system_message(provider)
    assert "_return" in prompt
    assert "Column names" in prompt


async def test_project_context_is_offered_when_given_and_absent_when_not():
    with_context = FakeProvider(responses=[json.dumps(PLAN)])
    await Planner(with_context, "fake-1").plan(QUESTION, context="Daily crypto, USD.")
    assert "Daily crypto, USD." in user_message(with_context)

    without = FakeProvider(responses=[json.dumps(PLAN)])
    await Planner(without, "fake-1").plan(QUESTION)
    assert "Daily crypto" not in user_message(without)


async def test_a_plan_naming_an_unknown_tool_is_retried_rather_than_returned():
    """The registry is the authority on what exists, not the model."""
    invented = {**PLAN, "steps": [{"id": "s1", "tool": "regress_vibes", "params": {}}]}
    provider = FakeProvider(responses=[json.dumps(invented), json.dumps(PLAN)])

    result = await Planner(provider, "fake-1").plan(QUESTION)

    assert len(provider.calls) == 2
    assert "unknown tool" in provider.calls[1].messages[-1].content
    assert [step.tool for step in result.output.steps] == ["adf", "variance_ratio"]


async def test_a_plan_that_stays_invalid_raises():
    invented = json.dumps({**PLAN, "steps": [{"id": "s1", "tool": "regress_vibes", "params": {}}]})
    provider = FakeProvider(responses=[invented, invented])

    with pytest.raises(AgentAttemptsExhaustedError) as excinfo:
        await Planner(provider, "fake-1").plan(QUESTION)

    assert excinfo.value.role == "planner"


async def test_the_result_carries_what_the_planning_cost():
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    result = await Planner(provider, "fake-1").plan(QUESTION)

    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0


# --- the escape hatch is invisible unless it is on ---------------------------


async def test_the_planner_is_not_told_about_code_steps_by_default():
    """Off by default has to reach the prompt, not only the executor.

    A Planner that knows the field exists will reach for it on a hard
    question, and every such plan would then be refused after the model call
    was already paid for.
    """
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    await Planner(provider, "fake-1").plan("Does BTC follow a random walk?")

    assert "code_steps" not in system_message(provider)


async def test_the_planner_learns_about_code_steps_when_the_sandbox_is_on():
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    await Planner(provider, "fake-1", code_sandbox=True).plan("Something exotic")

    text = system_message(provider)
    assert "code_steps" in text
    # It has to read as a last resort, or the registry stops being the default.
    assert "no tool" in text.lower()
    assert "unvalidated" in text.lower()


async def test_the_escape_hatch_section_renders_as_real_json():
    """`str.format` does not recurse into a substituted value.

    So the braces in `_ESCAPE_HATCH` must be single, not doubled the way the
    surrounding template's are — doubled ones would reach the model as
    `{{"id": "c1"...`, which is not JSON and is exactly the kind of prompt
    defect no assertion about wording would catch.
    """
    provider = FakeProvider(responses=[json.dumps(PLAN)])

    await Planner(provider, "fake-1", code_sandbox=True).plan("Something exotic")

    text = system_message(provider)
    assert '{"id": "c1"' in text
    assert "{{" not in text
