"""The escape hatch's place in a run.

Kept out of `test_orchestrator.py` for the reason the escape tests are kept
out of the runner's: this is the security-sensitive path, and a file that
mixes it with ordinary pipeline tests makes it easy to add a convenience and
not notice the gate moved.
"""

import json
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import pytest

from econometrica.agents.data_steward import DataSteward
from econometrica.agents.narrator import Narrator
from econometrica.agents.orchestrator import Orchestrator
from econometrica.agents.planner import Planner
from econometrica.agents.quant_coder import QuantCoder, is_sandbox_result
from econometrica.agents.validator import Validator
from econometrica.llm.fake import FakeProvider

QUESTION = "How dispersed are AAA's daily returns?"

CODE = (
    "spread = float(frame['AAA_return'].max() - frame['AAA_return'].min())\n"
    "result = {'scalars': {'spread': spread}}"
)

PLAN_WITH_CODE = {
    "question": QUESTION,
    "dataset": {"tickers": ["AAA"], "start": "2020-01-01", "end": "2020-06-30"},
    "steps": [{"id": "s1", "tool": "adf", "params": {"column": "AAA"}}],
    "code_steps": [{"id": "c1", "intent": "range of daily returns", "depends_on": ["s1"]}],
}

APPROVED = json.dumps({"approved": True, "reasons": ["the method is stated"]})


def draft(code: str = CODE, method: str = "Return range") -> str:
    return json.dumps({"method": method, "code": code, "rationale": "no tool computes this"})


def narrative(text: str = "The series wanders.") -> str:
    return json.dumps({"prose": text, "citations": ["s1"]})


@dataclass
class FakeSource:
    asked: list[str] = field(default_factory=list)

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        self.asked.append(ticker)
        index = pd.date_range("2020-01-01", periods=182, freq="D")
        rng = np.random.default_rng(7)
        return pd.Series(100.0 + np.cumsum(rng.normal(size=182)), index=index)


def build(
    *,
    plan: dict | None = None,
    drafts: list[str] | None = None,
    tier: str = "critic",
    code_sandbox: bool = True,
    with_coder: bool = True,
) -> tuple[Orchestrator, FakeProvider]:
    coder_fake = FakeProvider(name="q", responses=drafts or [draft()])
    planner_fake = FakeProvider(name="p", responses=[json.dumps(plan or PLAN_WITH_CODE)])
    return (
        Orchestrator(
            planners=[Planner(planner_fake, "fake-1")],
            steward=DataSteward(FakeSource(), min_obs=30),
            validator=Validator(FakeProvider(name="v", responses=[APPROVED]), "fake-1"),
            narrator=Narrator(FakeProvider(name="n", responses=[narrative()]), "fake-1"),
            coder=QuantCoder(coder_fake, "fake-1") if with_coder else None,
            code_sandbox=code_sandbox,
            tier=tier,
        ),
        coder_fake,
    )


# --- the gate ---------------------------------------------------------------


async def test_a_code_step_is_refused_when_the_sandbox_is_off() -> None:
    """Off by default is the design's word, and refusing is not degrading.

    Dropping the step silently would leave a plan whose recorded steps do not
    match its results — the run would look like it answered the question.
    """
    orchestrator, coder = build(code_sandbox=False)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "failed"
    assert "not enabled" in outcome.error
    assert coder.calls == []


async def test_a_code_step_is_refused_in_the_tier_that_skips_the_validator() -> None:
    orchestrator, coder = build(tier="single")

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "failed"
    assert "single" in outcome.error
    assert coder.calls == []


async def test_a_code_step_is_refused_when_no_coder_is_configured() -> None:
    orchestrator, _ = build(with_coder=False)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "failed"
    assert "quant coder" in outcome.error.lower()


async def test_an_ordinary_plan_never_touches_the_coder() -> None:
    """The overwhelming majority of runs. Turning the capability on is not
    consent to have a model write code for a question the registry answers."""
    plan = {**PLAN_WITH_CODE, "code_steps": []}
    orchestrator, coder = build(plan=plan)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert coder.calls == []


# --- what a permitted code step does ----------------------------------------


async def test_a_permitted_code_step_produces_a_marked_result() -> None:
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed", outcome.error
    step = outcome.execution.outcome("c1")
    assert step.status == "ran"
    assert step.result is not None
    assert is_sandbox_result(step.result) is True
    assert step.tool == "sandbox:return_range"


async def test_the_run_warns_that_an_unvalidated_method_was_used() -> None:
    """A reader must not have to notice a tool name to know this happened."""
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    assert any("unvalidated" in warning for warning in outcome.warnings)
    assert any("c1" in warning for warning in outcome.warnings)


async def test_the_validator_sees_the_generated_result() -> None:
    """Sign-off is mandatory, so the thing being signed off has to be there.

    A Validator shown only the registry steps would approve a run whose
    riskiest number it never saw.
    """
    orchestrator, _ = build()
    validator = orchestrator.validator
    assert validator is not None

    await orchestrator.run(QUESTION)

    asked = "\n".join(
        message.content for message in validator.provider.calls[0].messages  # type: ignore[attr-defined]
    )
    assert "sandbox:return_range" in asked


async def test_the_code_step_appears_in_the_trace_with_its_model() -> None:
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    coder_steps = [record for record in outcome.trace if record.agent == "quant_coder"]
    assert coder_steps, [record.agent for record in outcome.trace]
    assert coder_steps[0].kind == "llm"
    assert coder_steps[0].model == "fake-1"
    tool_steps = [
        record for record in outcome.trace if record.tool and record.tool.startswith("sandbox:")
    ]
    assert tool_steps and tool_steps[0].status == "ok"


async def test_a_code_step_that_fails_does_not_lose_the_rest_of_the_run() -> None:
    """The same contract a refused registry step keeps.

    One step that could not produce a number must not discard the ones that
    did — the Validator needs to see both.
    """
    broken = draft("result = {'scalars': {'a': 1 / 0}}")
    orchestrator, _ = build(drafts=[broken, broken])

    outcome = await orchestrator.run(QUESTION)

    assert outcome.execution.outcome("c1").status == "failed"
    assert "ZeroDivisionError" in outcome.execution.outcome("c1").error
    assert outcome.execution.outcome("s1").status == "ran"


async def test_a_code_step_that_tried_to_escape_is_recorded_as_refused() -> None:
    """`refused` rather than `failed`, for the reason the Econometrician uses
    the word: the system declined, it did not break."""
    escape = draft("import socket\nresult = {'scalars': {'a': 1.0}}")
    orchestrator, _ = build(drafts=[escape, escape])

    outcome = await orchestrator.run(QUESTION)

    step = outcome.execution.outcome("c1")
    assert step.status == "refused"
    assert "socket" in step.error


@pytest.mark.parametrize("tier", ["critic", "consensus"])
async def test_both_reviewed_tiers_permit_the_sandbox(tier: str) -> None:
    orchestrator, _ = build(tier=tier)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed", outcome.error
