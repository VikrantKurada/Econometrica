"""The Validator reviews a finished run — on facts, not on impressions."""

import json

import pandas as pd
import pytest

from econometrica.agents.base import AgentAttemptsExhaustedError
from econometrica.agents.econometrician import Econometrician
from econometrica.agents.schemas import AnalysisPlan, DatasetSpec, PlanStep
from econometrica.agents.validator import Validator, independence_warning
from econometrica.econ.diagnostics.engine import run_diagnostics
from econometrica.llm.fake import FakeProvider
from tests.econ.fixtures import make_garch_series, make_random_walk

APPROVED = json.dumps({"approved": True, "reasons": ["diagnostics support the conclusion"]})


def plan(*steps: PlanStep) -> AnalysisPlan:
    return AnalysisPlan(
        question="Is BTC volatility persistent?",
        dataset=DatasetSpec(tickers=["BTC-USD"], start="2020-01-01", end="2024-01-01"),
        steps=list(steps or (PlanStep(id="s1", tool="garch", params={"column": "r"}),)),
    )


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "r": make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=800, seed=3).to_numpy(),
            "walk": make_random_walk(n=800, seed=5).to_numpy(),
        }
    )


async def report(*steps: PlanStep):
    the_plan = plan(*steps)
    return the_plan, await Econometrician().run(the_plan, frame())


def prompt(provider: FakeProvider, call: int = 0) -> str:
    return "\n".join(message.content for message in provider.calls[call].messages)


async def test_an_approval_comes_back_as_a_verdict():
    the_plan, execution = await report()
    provider = FakeProvider(responses=[APPROVED])

    result = await Validator(provider, "fake-1").review(the_plan, execution)

    assert result.output.approved is True
    assert result.output.reasons


async def test_diagnostics_reach_the_model_as_numbers_not_as_a_question():
    """The Validator is never asked to infer whether residuals are well behaved.

    The whole reason a deterministic engine runs first is that an LLM asked to
    judge heteroskedasticity from prose will confabulate. It gets the statistic.
    """
    the_plan, execution = await report()
    diagnostics = run_diagnostics(pd.Series(frame()["r"]))
    arch = next(d for d in diagnostics if d.name == "arch_lm")
    provider = FakeProvider(responses=[APPROVED])

    await Validator(provider, "fake-1").review(the_plan, execution, diagnostics=diagnostics)

    text = prompt(provider)
    assert f"{arch.statistic:.6g}" in text
    assert "arch_lm" in text


async def test_gate_refusals_are_put_in_front_of_the_validator():
    """A refused step is evidence about the plan, not an absence of evidence."""
    the_plan, execution = await report(
        PlanStep(id="s1", tool="garch", params={"column": "walk"})
    )
    provider = FakeProvider(responses=[APPROVED])

    await Validator(provider, "fake-1").review(the_plan, execution)

    assert execution.refusals
    assert "refused" in prompt(provider).lower()
    assert "arch_effects" in prompt(provider)


async def test_unjudged_checks_are_disclosed_to_the_validator():
    short = pd.DataFrame({"r": [0.01, -0.02, 0.015, 0.0, -0.01]})
    the_plan = plan()
    execution = await Econometrician().run(the_plan, short)
    provider = FakeProvider(responses=[APPROVED])

    await Validator(provider, "fake-1").review(the_plan, execution)

    assert execution.unjudged
    assert "not judged" in prompt(provider).lower()


async def test_a_rejection_naming_a_real_step_is_accepted():
    the_plan, execution = await report()
    rejection = json.dumps(
        {"approved": False, "reasons": ["the window is too short"], "revise_steps": ["s1"]}
    )
    provider = FakeProvider(responses=[rejection])

    result = await Validator(provider, "fake-1").review(the_plan, execution)

    assert result.output.approved is False
    assert result.output.revise_steps == ["s1"]


async def test_a_rejection_naming_a_step_that_does_not_exist_is_retried():
    """An unactionable rejection is the failure mode this catches.

    "Revise step s9" stops the run and tells the next attempt nothing, because
    there is no s9 to revise.
    """
    the_plan, execution = await report()
    invented = json.dumps(
        {"approved": False, "reasons": ["something"], "revise_steps": ["s9"]}
    )
    good = json.dumps({"approved": False, "reasons": ["something"], "revise_steps": ["s1"]})
    provider = FakeProvider(responses=[invented, good])

    result = await Validator(provider, "fake-1").review(the_plan, execution)

    assert len(provider.calls) == 2
    assert "s9" in provider.calls[1].messages[-1].content
    assert result.output.revise_steps == ["s1"]


async def test_a_validator_that_keeps_inventing_steps_raises():
    the_plan, execution = await report()
    invented = json.dumps({"approved": False, "reasons": ["x"], "revise_steps": ["s9"]})
    provider = FakeProvider(responses=[invented, invented])

    with pytest.raises(AgentAttemptsExhaustedError):
        await Validator(provider, "fake-1").review(the_plan, execution)


# --- independence -----------------------------------------------------------


def test_the_same_provider_on_both_roles_is_warned_about():
    """A model reviewing its own reasoning is not a second opinion."""
    warning = independence_warning(econometrician="ollama", validator="ollama")

    assert warning is not None
    assert "ollama" in warning


def test_different_providers_draw_no_warning():
    assert independence_warning(econometrician="anthropic", validator="openai") is None


def test_the_econometrician_being_deterministic_is_not_a_clash():
    """Nothing is being marked against itself when one side has no model."""
    assert independence_warning(econometrician=None, validator="ollama") is None
