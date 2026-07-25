"""The Narrator writes the interpretation — and cannot publish a number
nothing computed."""

import json

import pandas as pd

from econometrica.agents.econometrician import Econometrician
from econometrica.agents.narrator import Narrator
from econometrica.agents.schemas import AnalysisPlan, DatasetSpec, PlanStep, ValidationVerdict
from econometrica.llm.fake import FakeProvider
from tests.econ.fixtures import make_garch_series, make_random_walk


def draft(prose: str, citations: list[str] | None = None) -> str:
    return json.dumps({"prose": prose, "citations": citations or ["s1"]})


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "r": make_garch_series(omega=1e-6, alpha=0.09, beta=0.90, n=800, seed=3).to_numpy(),
            "walk": make_random_walk(n=800, seed=5).to_numpy(),
        }
    )


def plan(*steps: PlanStep) -> AnalysisPlan:
    return AnalysisPlan(
        question="Is BTC volatility persistent?",
        dataset=DatasetSpec(tickers=["BTC-USD"], start="2020-01-01", end="2024-01-01"),
        steps=list(steps or (PlanStep(id="s1", tool="garch", params={"column": "r"}),)),
    )


async def run(*steps: PlanStep):
    the_plan = plan(*steps)
    return the_plan, await Econometrician().run(the_plan, frame())


def a_real_number(execution) -> float:
    return execution.outcome("s1").result.estimates[0].value


def prompt(provider: FakeProvider, call: int = 0) -> str:
    return "\n".join(message.content for message in provider.calls[call].messages)


async def test_a_grounded_draft_is_published():
    the_plan, execution = await run()
    value = a_real_number(execution)
    provider = FakeProvider(responses=[draft(f"The coefficient is {value:.4f}.")])

    outcome = await Narrator(provider, "fake-1").write(the_plan, execution)

    assert outcome.published is True
    assert outcome.narrative is not None
    assert outcome.grounding.grounded is True


async def test_a_draft_citing_a_number_nothing_computed_is_not_published():
    """The safeguard, exercised end to end through the agent."""
    the_plan, execution = await run()
    provider = FakeProvider(responses=[draft("Beta is 4.4444."), draft("Beta is 4.4444.")])

    outcome = await Narrator(provider, "fake-1").write(the_plan, execution)

    assert outcome.published is False
    assert outcome.narrative is None
    assert any(issue.value == 4.4444 for issue in outcome.grounding.issues)


async def test_the_retry_tells_the_model_which_number_was_wrong():
    """"That was ungrounded" gets the same draft back; naming 4.4444 does not."""
    the_plan, execution = await run()
    value = a_real_number(execution)
    provider = FakeProvider(
        responses=[draft("Beta is 4.4444."), draft(f"Beta is {value:.4f}.")]
    )

    outcome = await Narrator(provider, "fake-1").write(the_plan, execution)

    assert outcome.published is True
    assert "4.4444" in provider.calls[1].messages[-1].content


async def test_citations_must_name_steps_that_exist():
    the_plan, execution = await run()
    value = a_real_number(execution)
    text = f"The coefficient is {value:.4f}."
    provider = FakeProvider(
        responses=[draft(text, ["s9"]), draft(text, ["s1"])]
    )

    outcome = await Narrator(provider, "fake-1").write(the_plan, execution)

    assert outcome.published is True
    assert "s9" in provider.calls[1].messages[-1].content


async def test_the_results_reach_the_model_as_numbers():
    the_plan, execution = await run()
    provider = FakeProvider(responses=[draft("Nothing numeric here.")])

    await Narrator(provider, "fake-1").write(the_plan, execution)

    assert "s1" in prompt(provider)
    assert "garch" in prompt(provider)


async def test_the_validators_verdict_reaches_the_model():
    the_plan, execution = await run()
    provider = FakeProvider(responses=[draft("Nothing numeric here.")])
    verdict = ValidationVerdict(approved=False, reasons=["the sample is too short"])

    await Narrator(provider, "fake-1").write(the_plan, execution, verdict=verdict)

    assert "the sample is too short" in prompt(provider)


async def test_refused_steps_are_disclosed_so_the_prose_can_say_so():
    the_plan, execution = await run(PlanStep(id="s1", tool="garch", params={"column": "walk"}))
    provider = FakeProvider(responses=[draft("No volatility model was fitted.", [])])

    outcome = await Narrator(provider, "fake-1").write(the_plan, execution)

    assert outcome.published is True
    assert "refused" in prompt(provider).lower()


async def test_an_empty_draft_is_rejected():
    the_plan, execution = await run()
    value = a_real_number(execution)
    provider = FakeProvider(
        responses=[draft(""), draft(f"The coefficient is {value:.4f}.")]
    )

    outcome = await Narrator(provider, "fake-1").write(the_plan, execution)

    assert outcome.published is True
    assert len(provider.calls) == 2
