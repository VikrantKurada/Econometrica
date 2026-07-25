"""The orchestrator runs the whole pipeline and streams its progress."""

import json
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import pytest

from econometrica.agents.data_steward import DataSteward
from econometrica.agents.narrator import Narrator
from econometrica.agents.orchestrator import Orchestrator, RunEvent
from econometrica.agents.planner import Planner
from econometrica.agents.validator import Validator
from econometrica.llm.errors import ProviderUnavailableError
from econometrica.llm.fake import FakeProvider

QUESTION = "Does AAA follow a random walk?"

PLAN = {
    "question": QUESTION,
    "dataset": {"tickers": ["AAA"], "start": "2020-01-01", "end": "2020-06-30"},
    "steps": [
        {"id": "s1", "tool": "adf", "params": {"column": "AAA"}},
        {"id": "s2", "tool": "kpss", "params": {"column": "AAA"}, "depends_on": ["s1"]},
    ],
}

GARCH_PLAN = {
    **PLAN,
    "steps": [{"id": "s1", "tool": "garch", "params": {"column": "AAA_return"}}],
}

APPROVED = json.dumps({"approved": True, "reasons": ["the diagnostics agree"]})
REJECTED = json.dumps(
    {"approved": False, "reasons": ["the window is too short"], "revise_steps": ["s1"]}
)


def narrative(text: str = "The series wanders without direction.") -> str:
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
    plans: list[str] | None = None,
    verdicts: list[str] | None = None,
    prose: list[str] | None = None,
    tier: str = "critic",
    planner_providers: list[FakeProvider] | None = None,
) -> tuple[Orchestrator, dict[str, FakeProvider]]:
    planner_fakes = planner_providers or [
        FakeProvider(name="p", responses=plans or [json.dumps(PLAN)])
    ]
    validator_fake = FakeProvider(name="v", responses=verdicts or [APPROVED])
    narrator_fake = FakeProvider(name="n", responses=prose or [narrative()])

    orchestrator = Orchestrator(
        planners=[Planner(fake, "fake-1") for fake in planner_fakes],
        steward=DataSteward(FakeSource(), min_obs=30),
        validator=Validator(validator_fake, "fake-1"),
        narrator=Narrator(narrator_fake, "fake-1"),
        tier=tier,
    )
    return orchestrator, {
        "planner": planner_fakes[0],
        "validator": validator_fake,
        "narrator": narrator_fake,
    }


# --- the happy path ---------------------------------------------------------


async def test_a_full_run_produces_a_plan_data_results_and_prose():
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert outcome.plan is not None
    assert outcome.quality is not None
    assert set(outcome.execution.results) == {"s1", "s2"}
    assert outcome.verdict is not None and outcome.verdict.approved
    assert outcome.narration is not None and outcome.narration.published


async def test_events_arrive_in_pipeline_order():
    orchestrator, _ = build()

    names = [event.name async for event in orchestrator.stream(QUESTION)]

    assert names[0] == "run.started"
    assert names[-1] == "run.finished"
    assert names.index("plan.finished") < names.index("data.finished")
    assert names.index("data.finished") < names.index("step.finished")
    assert names.count("step.finished") == 2
    assert names.index("validate.finished") < names.index("narrate.finished")


async def test_the_terminal_event_carries_the_whole_outcome():
    orchestrator, _ = build()

    events: list[RunEvent] = [event async for event in orchestrator.stream(QUESTION)]

    assert events[-1].payload["status"] == "completed"


# --- tiers ------------------------------------------------------------------


async def test_the_single_tier_skips_the_validator_but_keeps_the_gates():
    """Cheap does not mean unguarded: the deterministic gates always run."""
    orchestrator, fakes = build(plans=[json.dumps(GARCH_PLAN)], tier="single")

    outcome = await orchestrator.run(QUESTION)

    assert outcome.verdict is None
    assert fakes["validator"].calls == []
    # A random walk's returns carry no ARCH effects, so the gate still refused.
    assert outcome.execution.refusals


async def test_the_critic_tier_consults_the_validator():
    orchestrator, fakes = build(tier="critic")

    outcome = await orchestrator.run(QUESTION)

    assert len(fakes["validator"].calls) == 1
    assert outcome.verdict is not None


async def test_consensus_surfaces_disagreement_rather_than_resolving_it():
    """Two planners, two different plans. The user is told, not arbitrated for."""
    other = {**PLAN, "steps": [{"id": "s1", "tool": "kpss", "params": {"column": "AAA"}}]}
    orchestrator, _ = build(
        tier="consensus",
        planner_providers=[
            FakeProvider(name="p1", responses=[json.dumps(PLAN)]),
            FakeProvider(name="p2", responses=[json.dumps(other)]),
        ],
    )

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert any("disagree" in warning for warning in outcome.warnings)
    assert outcome.alternative_plans


async def test_consensus_says_so_when_the_planners_agree():
    orchestrator, _ = build(
        tier="consensus",
        planner_providers=[
            FakeProvider(name="p1", responses=[json.dumps(PLAN)]),
            FakeProvider(name="p2", responses=[json.dumps(PLAN)]),
        ],
    )

    outcome = await orchestrator.run(QUESTION)

    assert not any("disagree" in warning for warning in outcome.warnings)


# --- the revision loop ------------------------------------------------------


async def test_a_rejection_triggers_exactly_one_revision():
    orchestrator, fakes = build(
        plans=[json.dumps(PLAN), json.dumps(PLAN)], verdicts=[REJECTED, APPROVED]
    )

    outcome = await orchestrator.run(QUESTION)

    assert outcome.revisions == 1
    assert len(fakes["planner"].calls) == 2
    assert outcome.verdict is not None and outcome.verdict.approved


async def test_the_revision_carries_the_validators_reasons_back_to_the_planner():
    orchestrator, fakes = build(
        plans=[json.dumps(PLAN), json.dumps(PLAN)], verdicts=[REJECTED, APPROVED]
    )

    await orchestrator.run(QUESTION)

    second = "\n".join(m.content for m in fakes["planner"].calls[1].messages)
    assert "the window is too short" in second


async def test_a_second_rejection_ends_the_run_rather_than_looping():
    orchestrator, fakes = build(
        plans=[json.dumps(PLAN), json.dumps(PLAN)], verdicts=[REJECTED, REJECTED]
    )

    outcome = await orchestrator.run(QUESTION)

    assert outcome.revisions == 1
    assert len(fakes["validator"].calls) == 2
    assert outcome.verdict is not None and outcome.verdict.approved is False
    # A rejection is information, not a crash: the results still come back.
    assert outcome.execution.results


# --- failure ----------------------------------------------------------------


async def test_a_provider_failure_leaves_a_readable_outcome():
    """The same contract messages.py keeps: never a half-written run."""
    broken = FakeProvider(name="p", error=ProviderUnavailableError("p", "daemon down"))
    orchestrator, _ = build(planner_providers=[broken])

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "failed"
    assert "daemon down" in outcome.error
    assert outcome.plan is None


async def test_a_failure_still_closes_the_stream():
    broken = FakeProvider(name="p", error=ProviderUnavailableError("p", "daemon down"))
    orchestrator, _ = build(planner_providers=[broken])

    names = [event.name async for event in orchestrator.stream(QUESTION)]

    assert names[-1] == "run.finished"
    assert "run.failed" in names


async def test_ungrounded_prose_blocks_publication_without_losing_the_results():
    orchestrator, _ = build(prose=[narrative("Beta is 9.87."), narrative("Beta is 9.87.")])

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "blocked"
    assert outcome.narration is not None and outcome.narration.published is False
    assert outcome.execution.results


# --- the trace --------------------------------------------------------------


async def test_the_trace_records_every_model_call_and_tool_step():
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    agents = [record.agent for record in outcome.trace]
    assert agents == [
        "planner",
        "data_steward",
        "econometrician",
        "econometrician",
        "validator",
        "narrator",
    ]
    tools = [r.tool for r in outcome.trace if r.agent == "econometrician"]
    assert tools == ["adf", "kpss"]


async def test_every_step_after_the_first_is_reachable_from_it():
    """A trace with an orphan cannot be drawn as one DAG."""
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    assert outcome.trace[0].parent is None
    for index, record in enumerate(outcome.trace[1:], start=1):
        assert record.parent is not None, f"step {index} ({record.agent}) has no parent"
        assert record.parent < index, "a step may only follow an earlier one"


async def test_a_rejected_attempt_is_its_own_traced_step():
    """Otherwise the cost dashboard understates every run that needed a retry."""
    orchestrator, _ = build(prose=[narrative("Beta is 9.87."), narrative()])

    outcome = await orchestrator.run(QUESTION)

    narrator_steps = [r for r in outcome.trace if r.agent == "narrator"]
    assert [r.attempt for r in narrator_steps] == [1, 2]
    assert narrator_steps[0].status == "failed"
    assert narrator_steps[0].usage.output_tokens > 0, "a rejected draft still cost tokens"


async def test_a_withheld_narration_is_traced_as_refused_not_ok():
    """The trace has to show the gate firing, or the safeguard is invisible."""
    orchestrator, _ = build(prose=[narrative("Beta is 9.87."), narrative("Beta is 9.87.")])

    outcome = await orchestrator.run(QUESTION)

    narrator_steps = [r for r in outcome.trace if r.agent == "narrator"]
    assert narrator_steps[-1].status == "refused"
    assert "9.87" in narrator_steps[-1].detail


async def test_tool_steps_carry_a_hash_of_what_they_computed():
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    hashes = [r.tool_call_hash for r in outcome.trace if r.kind == "tool" and r.tool]
    assert all(value and len(value) == 64 for value in hashes)
    assert len(set(hashes)) == len(hashes), "adf and kpss must not collide"


async def test_a_consensus_run_traces_the_planners_it_did_not_use():
    """Hiding the losers would misreport what the tier costs."""
    other = {**PLAN, "steps": [{"id": "s1", "tool": "kpss", "params": {"column": "AAA"}}]}
    orchestrator, _ = build(
        tier="consensus",
        planner_providers=[
            FakeProvider(name="p1", responses=[json.dumps(PLAN)]),
            FakeProvider(name="p2", responses=[json.dumps(other)]),
        ],
    )

    outcome = await orchestrator.run(QUESTION)

    planners = [r for r in outcome.trace if r.agent == "planner"]
    assert {r.provider for r in planners} == {"p1", "p2"}


# --- independence -----------------------------------------------------------


async def test_a_validator_sharing_the_planners_provider_is_warned_about():
    shared = FakeProvider(name="ollama", responses=[json.dumps(PLAN)])
    orchestrator = Orchestrator(
        planners=[Planner(shared, "fake-1")],
        steward=DataSteward(FakeSource(), min_obs=30),
        validator=Validator(FakeProvider(name="ollama", responses=[APPROVED]), "fake-1"),
        narrator=Narrator(FakeProvider(name="n", responses=[narrative()]), "fake-1"),
    )

    outcome = await orchestrator.run(QUESTION)

    assert any("blind spots" in warning for warning in outcome.warnings)


async def test_an_unknown_tier_is_refused_at_construction():
    with pytest.raises(ValueError, match="tier"):
        Orchestrator(
            planners=[Planner(FakeProvider(), "fake-1")],
            steward=DataSteward(FakeSource()),
            validator=None,
            narrator=Narrator(FakeProvider(), "fake-1"),
            tier="vibes",
        )
