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
from econometrica.agents.query_writer import QueryWriter
from econometrica.agents.validator import Validator
from econometrica.charts.spec import unresolved_references
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

#: A tool that emits a series, so the run has something chartable. GARCH does
#: not qualify here: the gates refuse it on a random walk, which is the point
#: of the refusal test below.
VOL_PLAN = {
    **PLAN,
    "steps": [
        {"id": "s1", "tool": "realized_vol", "params": {"column": "AAA_return", "window": 20}}
    ],
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


@dataclass
class WindowSource:
    """Honours the window it is asked for, and remembers every one of them."""

    windows: list[tuple[date, date]] = field(default_factory=list)

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        self.windows.append((start, end))
        index = pd.date_range(start, end, freq="D")
        rng = np.random.default_rng(7)
        return pd.Series(100.0 + np.cumsum(rng.normal(size=len(index))), index=index)


def build(
    *,
    plans: list[str] | None = None,
    verdicts: list[str] | None = None,
    prose: list[str] | None = None,
    tier: str = "critic",
    planner_providers: list[FakeProvider] | None = None,
    source: object | None = None,
    searcher: object | None = None,
    web_search: bool = False,
    query_writer_provider: FakeProvider | None = None,
    retriever: object | None = None,
    researcher: object | None = None,
) -> tuple[Orchestrator, dict[str, FakeProvider]]:
    planner_fakes = planner_providers or [
        FakeProvider(name="p", responses=plans or [json.dumps(PLAN)])
    ]
    validator_fake = FakeProvider(name="v", responses=verdicts or [APPROVED])
    narrator_fake = FakeProvider(name="n", responses=prose or [narrative()])
    query_writer = (
        QueryWriter(query_writer_provider, "fake-1")
        if query_writer_provider is not None
        else None
    )

    orchestrator = Orchestrator(
        planners=[Planner(fake, "fake-1") for fake in planner_fakes],
        steward=DataSteward(source or FakeSource(), min_obs=30),
        validator=Validator(validator_fake, "fake-1"),
        narrator=Narrator(narrator_fake, "fake-1"),
        tier=tier,
        searcher=searcher,
        web_search=web_search,
        query_writer=query_writer,
        retriever=retriever,
        researcher=researcher,
    )
    fakes = {
        "planner": planner_fakes[0],
        "validator": validator_fake,
        "narrator": narrator_fake,
    }
    if query_writer_provider is not None:
        fakes["query_writer"] = query_writer_provider
    return orchestrator, fakes


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

# --- charts -----------------------------------------------------------------


async def test_a_run_proposes_charts_bound_to_the_step_that_produced_them():
    # Without this the canvas has nothing to draw: the Visualizer exists but
    # no run ever called it, so RunOutcome carried no charts at all.
    orchestrator, _ = build(plans=[json.dumps(VOL_PLAN)])

    outcome = await orchestrator.run(QUESTION)

    assert outcome.charts, "a volatility path supports a line chart"
    assert {chart.step_id for chart in outcome.charts} == {"s1"}
    assert "line" in {chart.type for chart in outcome.charts}


async def test_every_proposed_chart_binds_to_the_result_it_cites():
    # The same invariant charts/propose.py holds, asserted through the whole
    # pipeline: a chart of data the result does not carry is an ungrounded
    # number with a line drawn through it.
    orchestrator, _ = build(plans=[json.dumps(VOL_PLAN)])

    outcome = await orchestrator.run(QUESTION)

    results = outcome.execution.results
    assert outcome.charts
    for chart in outcome.charts:
        assert unresolved_references(chart, results[chart.step_id]) == []


async def test_a_refused_step_contributes_no_charts():
    # Charts come from results. A step the gates refused has none, and drawing
    # something for it would show a reader an analysis that did not happen.
    # This plan fits a GARCH to a random walk, which the gates decline.
    orchestrator, _ = build(plans=[json.dumps(GARCH_PLAN)])

    outcome = await orchestrator.run(QUESTION)

    assert [step.status for step in outcome.execution.outcomes] == ["refused"]
    assert outcome.charts == []


async def test_the_chart_phase_is_announced():
    orchestrator, _ = build(plans=[json.dumps(VOL_PLAN)])

    names = [event.name async for event in orchestrator.stream(QUESTION)]

    assert names.index("step.finished") < names.index("charts.finished")
    assert names.index("charts.finished") < names.index("narrate.finished")


async def test_a_revised_plan_runs_on_the_data_it_asked_for():
    """A revision may change the window, and the run must follow it.

    The dataset used to be resolved once, before the revision loop, so a
    revised plan executed against the *previous* plan's frame. The stored run
    then contradicted itself: `plan.dataset` named one window and the results
    came from another, which makes the plan a wrong account of its own numbers
    and makes re-running it from the manifest disagree for no good reason.
    """
    revised = {
        **PLAN,
        "dataset": {"tickers": ["AAA"], "start": "2020-03-01", "end": "2020-06-30"},
    }
    source = WindowSource()
    orchestrator, _ = build(
        plans=[json.dumps(PLAN), json.dumps(revised)],
        verdicts=[REJECTED, APPROVED],
        source=source,
    )

    outcome = await orchestrator.run(QUESTION)

    assert outcome.plan is not None and outcome.quality is not None
    assert outcome.plan.dataset.start == date(2020, 3, 1)
    # The window the data actually came from, not the one the first plan asked
    # for. These two disagreeing is the bug.
    assert source.windows[-1] == (date(2020, 3, 1), date(2020, 6, 30))
    assert outcome.quality.start == outcome.plan.dataset.start


async def test_an_unchanged_dataset_is_not_fetched_twice():
    # A revision usually keeps the same data and changes the method. Re-fetching
    # it would double the cost of every revised run for nothing.
    source = WindowSource()
    orchestrator, _ = build(
        plans=[json.dumps(PLAN), json.dumps(PLAN)],
        verdicts=[REJECTED, APPROVED],
        source=source,
    )

    await orchestrator.run(QUESTION)

    assert len(source.windows) == 1


# --- web search -------------------------------------------------------------


class SpyProvider:
    """Records that it was asked, which is the assertion for the disabled case."""

    name = "spy"

    def __init__(self, results=None, boom: str = "") -> None:
        self.results = results or []
        self.boom = boom
        self.asked: list[str] = []

    async def search(self, query: str, *, limit: int = 5):
        self.asked.append(query)
        if self.boom:
            raise RuntimeError(self.boom)
        return self.results


def a_result():
    from econometrica.tools.web_search import SearchResult

    return SearchResult(
        title="Nifty 50 index",
        url="https://example.invalid/nifty",
        snippet="The Nifty 50 trades under the symbol ^NSEI.",
    )


def planner_prompt(fakes) -> str:
    """Everything the Planner's first turn was sent, as one string."""
    return "\n".join(message.content for message in fakes["planner"].calls[0].messages)


def search_step(outcome):
    return next(step for step in outcome.trace if (step.tool or "").startswith("web_search:"))


#: The query a scripted writer emits. The fake does not reason about the
#: question — it returns this — so a test asserts the searcher was handed
#: exactly this, which the verbatim question never was.
QUERY = "Nifty 50 ticker symbol Yahoo Finance"


def a_query(*queries: str) -> FakeProvider:
    """A writer fake that emits `queries` (defaulting to the one QUERY)."""
    payload = {"queries": list(queries) or [QUERY]}
    return FakeProvider(name="q", responses=[json.dumps(payload)])


def query_writer_step(outcome):
    return next(s for s in outcome.trace if s.agent == "query_writer" and s.kind == "llm")


async def test_a_model_written_query_is_searched_not_the_verbatim_question():
    provider = SpyProvider([a_result()])
    orchestrator, fakes = build(
        searcher=provider, web_search=True, query_writer_provider=a_query()
    )

    await orchestrator.run(QUESTION)

    assert provider.asked == [QUERY]  # the generated query, not QUESTION
    # The header is what tells the model this text is read, not computed.
    assert "read from the web, not computed" in planner_prompt(fakes)
    assert "^NSEI" in planner_prompt(fakes)  # the results still reach the Planner


async def test_without_a_query_writer_the_verbatim_question_is_the_floor():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(searcher=provider, web_search=True)  # no writer

    await orchestrator.run(QUESTION)

    assert provider.asked == [QUESTION]


async def test_at_most_three_queries_are_searched():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=a_query(*(f"q{n} ticker symbol" for n in range(5))),
    )

    await orchestrator.run(QUESTION)

    assert len(provider.asked) == 3


async def test_a_writer_that_will_not_answer_falls_back_to_the_verbatim_question():
    provider = SpyProvider([a_result()])
    # Two unparseable replies exhaust the writer's attempts.
    orchestrator, _ = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(name="q", responses=["not json", "still not json"]),
    )

    outcome = await orchestrator.run(QUESTION)

    assert provider.asked == [QUESTION]
    assert outcome.status == "completed"


async def test_a_disabled_search_never_reaches_the_provider():
    """Asserted on the spy, not on the outcome.

    An outcome-level assertion would pass just as well if the provider had been
    called and its results dropped, which is the bug worth preventing.
    """
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider, web_search=False, query_writer_provider=a_query()
    )

    await orchestrator.run(QUESTION)

    assert provider.asked == []


async def test_a_failed_search_degrades_the_run_rather_than_failing_it():
    provider = SpyProvider(boom="the endpoint returned 503")
    orchestrator, fakes = build(
        searcher=provider, web_search=True, query_writer_provider=a_query()
    )

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert "read from the web" not in planner_prompt(fakes)
    step = search_step(outcome)
    assert step.status == "failed"
    assert "503" in step.detail


async def test_the_search_step_records_the_query_it_ran():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider, web_search=True, query_writer_provider=a_query()
    )

    outcome = await orchestrator.run(QUESTION)

    step = search_step(outcome)
    assert step.kind == "tool"
    assert step.agent == "planner"  # the search feeds the planner
    assert step.prompt == QUERY  # the query it ran, not the question
    assert "^NSEI" in step.response


async def test_the_writer_turn_precedes_the_search_and_the_plan():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider, web_search=True, query_writer_provider=a_query()
    )

    outcome = await orchestrator.run(QUESTION)

    writer = outcome.trace.index(query_writer_step(outcome))
    search = outcome.trace.index(search_step(outcome))
    plan = next(
        i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm"
    )
    assert writer < search < plan


# --- retrieval --------------------------------------------------------------


class SpyRetriever:
    """A retriever that returns a scripted outcome and records the query."""

    def __init__(self, *, hits=None, fail: str = "") -> None:
        self.model = "spy-embed"
        self._hits = [] if hits is None else hits
        self._fail = fail
        self.asked: list[str] = []

    async def fetch(self, query: str):
        from econometrica.tools.retrieval import RetrievalOutcome

        self.asked.append(query)
        if self._fail:
            return RetrievalOutcome(model=self.model, query=query, failed=True, detail=self._fail)
        return RetrievalOutcome(model=self.model, query=query, hits=self._hits)


def a_passage():
    from uuid import uuid4

    from econometrica.tools.retrieval import Retrieved

    return Retrieved(
        document_id=uuid4(),
        document_name="methodology.md",
        ordinal=0,
        text="Use the Fama-French five-factor model for this asset class.",
        score=0.9,
    )


def retrieval_step(outcome):
    return next(s for s in outcome.trace if (s.tool or "").startswith("retrieval:"))


async def test_retrieved_passages_reach_the_planner():
    spy = SpyRetriever(hits=[a_passage()])
    orchestrator, fakes = build(retriever=spy)

    await orchestrator.run(QUESTION)

    assert spy.asked == [QUESTION]
    assert "read from documents, not computed" in planner_prompt(fakes)
    assert "Fama-French five-factor" in planner_prompt(fakes)


async def test_without_a_retriever_nothing_is_retrieved():
    orchestrator, _ = build()  # retriever is None

    outcome = await orchestrator.run(QUESTION)

    assert not any((s.tool or "").startswith("retrieval:") for s in outcome.trace)


async def test_a_failed_retrieval_degrades_the_run():
    spy = SpyRetriever(fail="ollama unreachable")
    orchestrator, fakes = build(retriever=spy)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert "read from documents" not in planner_prompt(fakes)
    assert retrieval_step(outcome).status == "failed"


async def test_the_retrieval_step_precedes_the_plan():
    spy = SpyRetriever(hits=[a_passage()])
    orchestrator, _ = build(retriever=spy)

    outcome = await orchestrator.run(QUESTION)

    retrieval = outcome.trace.index(retrieval_step(outcome))
    plan = next(
        i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm"
    )
    assert retrieval < plan


# --- mcp research -----------------------------------------------------------


class SpyResearcher:
    def __init__(self, *, summary="", fail=False) -> None:
        from econometrica.agents.trace import StepRecord

        self._summary = summary
        self._fail = fail
        self.asked: list[str] = []
        self._Step = StepRecord

    async def research(self, question: str):
        from econometrica.agents.researcher import ResearchOutcome

        self.asked.append(question)
        rec = self._Step(agent="researcher", kind="llm", status="ok", response=self._summary)
        return ResearchOutcome(summary=self._summary, records=[rec], failed=self._fail)


def research_step(outcome):
    return next(s for s in outcome.trace if s.agent == "researcher")


async def test_a_research_summary_reaches_the_planner():
    spy = SpyResearcher(summary="The internal universe lists ^NSEI as the Nifty 50.")
    orchestrator, fakes = build(researcher=spy)

    await orchestrator.run(QUESTION)

    assert spy.asked == [QUESTION]
    assert "read, not computed" in planner_prompt(fakes).lower()
    assert "^NSEI" in planner_prompt(fakes)


async def test_without_a_researcher_nothing_is_researched():
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    assert not any(s.agent == "researcher" for s in outcome.trace)


async def test_the_research_steps_precede_the_plan():
    spy = SpyResearcher(summary="context")
    orchestrator, _ = build(researcher=spy)

    outcome = await orchestrator.run(QUESTION)

    research = outcome.trace.index(research_step(outcome))
    plan = next(i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm")
    assert research < plan
