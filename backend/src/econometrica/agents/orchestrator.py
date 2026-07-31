"""The orchestrator: one question, one run, streamed.

It composes the roles rather than reimplementing any of them, and the value it
adds is in three decisions that no single agent can make.

**Which tier.** `single` is cheap and skips the Validator; `critic` is the
default and consults it; `consensus` plans on several providers and reports
what they disagree about. The deterministic gates — tool preconditions and
numeric grounding — run in **every** tier. Cheap must not mean unguarded.

**When to stop.** A rejection buys exactly one revision. Left unbounded, a
Validator and a Planner will trade drafts until the budget runs out, and the
second rejection is far more likely to mean "this question cannot be answered
with this data" than "try once more".

**What a failure leaves behind.** The same contract `messages.py` keeps for
chat: a run that dies mid-pipeline comes back readable, saying how far it got
and why it stopped, never half-written.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from econometrica.agents.base import (
    PROMPT_LIMIT,
    AgentAttemptsExhaustedError,
    AgentRefusedError,
)
from econometrica.agents.data_steward import DataQualityReport, DataSteward
from econometrica.agents.econometrician import Econometrician, ExecutionReport, StepOutcome
from econometrica.agents.narrator import Narration, Narrator
from econometrica.agents.planner import Planner
from econometrica.agents.quant_coder import (
    QuantCoder,
    SandboxNotPermittedError,
    check_permitted,
)
from econometrica.agents.query_writer import QueryWriter
from econometrica.agents.schemas import AnalysisPlan, ValidationVerdict
from econometrica.agents.trace import StepRecord, TraceBuilder, tool_call_hash
from econometrica.agents.validator import Validator, independence_warning
from econometrica.charts.propose import propose_charts
from econometrica.charts.spec import ChartSpec
from econometrica.econ.diagnostics.engine import run_diagnostics
from econometrica.econ.types import Diagnostic
from econometrica.tools.web_search import SearchProvider, search

Tier = Literal["single", "critic", "consensus"]
TIERS: tuple[Tier, ...] = ("single", "critic", "consensus")

RunStatus = Literal["completed", "blocked", "failed"]

#: The most queries a run will search. A question naming two instruments needs
#: two lookups; a small headroom is cheap. Above this is a runaway model, not a
#: real question, and each query is a live call against a contract-free endpoint.
MAX_SEARCH_QUERIES = 3

#: The Econometrician's own status words, in the trace's vocabulary. "ran" is
#: the only one that differs, and only because "ok" reads better beside a
#: model call that returned.
_STEP_STATE = {"ran": "ok", "refused": "refused", "failed": "failed", "skipped": "skipped"}


class RunEvent(BaseModel):
    """One increment of progress.

    Dotted names rather than a discriminated union: a client renders a
    timeline, and new phases must not break one that has not been updated.
    """

    name: str
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RunOutcome(BaseModel):
    """Everything a run produced, however far it got."""

    status: RunStatus
    question: str
    plan: AnalysisPlan | None = None
    #: Populated only in the consensus tier, and only where planners differed.
    alternative_plans: list[AnalysisPlan] = Field(default_factory=list)
    quality: DataQualityReport | None = None
    execution: ExecutionReport | None = None
    verdict: ValidationVerdict | None = None
    narration: Narration | None = None
    #: What each result supports being drawn as, decided from its shape. Each
    #: carries the `step_id` whose `ResultSet` it draws from, because a chart
    #: that cannot be traced to the numbers under it is decoration.
    charts: list[ChartSpec] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    revisions: int = 0
    error: str = ""
    #: Every model call and tool invocation, in the order they happened, with
    #: parent links. `services.tracing` persists this; the trace viewer draws
    #: it. Rejected attempts are here too — they were billed.
    trace: list[StepRecord] = Field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        *,
        planners: Sequence[Planner],
        steward: DataSteward,
        validator: Validator | None,
        narrator: Narrator,
        coder: QuantCoder | None = None,
        code_sandbox: bool = False,
        searcher: SearchProvider | None = None,
        web_search: bool = False,
        query_writer: QueryWriter | None = None,
        tier: Tier = "critic",
        max_revisions: int = 1,
    ) -> None:
        if tier not in TIERS:
            raise ValueError(f"unknown validation tier {tier!r}; expected one of {TIERS}")
        if not planners:
            raise ValueError("at least one planner is required")

        self.planners = list(planners)
        self.steward = steward
        self.validator = validator
        self.narrator = narrator
        self.coder = coder
        #: The resolved capability, passed in rather than read here: `agents/`
        #: knows nothing about projects or chats, and `services.capabilities`
        #: is the one place that decides. Default False keeps every existing
        #: caller off the escape hatch without changing a line.
        self.code_sandbox = code_sandbox
        #: Passed in for the same reason `code_sandbox` is. Both are needed
        #: rather than one: a provider may be absent because none is configured
        #: on this deployment, which is not the same as search being off for
        #: this project.
        self.searcher = searcher
        self.web_search = web_search
        #: Writes the search queries when present. Absent means the verbatim
        #: question is searched — the floor this feature never does worse than.
        #: Passed in for the same reason `searcher` is: `agents/` decides nothing
        #: about projects; the router supplies one only when the role is assigned.
        self.query_writer = query_writer
        self.econometrician = Econometrician()
        self.tier: Tier = tier
        self.max_revisions = max_revisions

    async def run(self, question: str, *, context: str = "") -> RunOutcome:
        """Run to completion and return the outcome, discarding progress events."""
        outcome = RunOutcome(status="failed", question=question)
        async for event in self.stream(question, context=context):
            if event.name == "run.finished":
                outcome = RunOutcome.model_validate(event.payload)
        return outcome

    async def stream(self, question: str, *, context: str = "") -> AsyncIterator[RunEvent]:
        outcome = RunOutcome(status="failed", question=question)
        yield RunEvent(name="run.started", detail=question)

        try:
            async for event in self._pipeline(question, context, outcome):
                yield event
        except Exception as exc:
            # Deliberately broad. Whatever failed, the run has to come back
            # describing itself — a traceback on the wire tells a user nothing
            # and loses the work that did succeed.
            outcome.status = "failed"
            outcome.error = f"{type(exc).__name__}: {exc}"
            yield RunEvent(name="run.failed", detail=outcome.error)

        yield RunEvent(name="run.finished", payload=outcome.model_dump(mode="json"))

    # --- the pipeline -------------------------------------------------------

    async def _pipeline(
        self, question: str, context: str, outcome: RunOutcome
    ) -> AsyncIterator[RunEvent]:
        trace = TraceBuilder()
        outcome.trace = trace.records
        self._warn_about_independence(outcome)
        for warning in outcome.warnings:
            yield RunEvent(name="run.warning", detail=warning)

        context = await self._search_context(question, context, trace)
        plan = await self._plan(question, context, outcome, trace)
        # Before the data is fetched, not after: a run that is not permitted to
        # execute the code it planned should say so without spending a fetch on
        # it, and refusing beats silently dropping the step — that would leave
        # a plan whose recorded steps do not match its results.
        self._check_code_permission(plan)
        yield RunEvent(name="plan.finished", payload=plan.model_dump(mode="json"))
        for warning in outcome.warnings[len(outcome.warnings) - 1 :]:
            if "disagree" in warning:
                yield RunEvent(name="plan.disagreement", detail=warning)

        dataset = await self.steward.resolve(plan.dataset)
        outcome.quality = dataset.report
        trace.add(
            StepRecord(
                agent="data_steward",
                kind="tool",
                status="ok",
                parent=trace.last,
                detail=f"{dataset.report.rows} rows, {len(dataset.report.flags)} flag(s)",
            )
        )
        yield RunEvent(name="data.finished", payload=dataset.report.model_dump(mode="json"))

        revision_context = context
        while True:
            execution = await self.econometrician.run(plan, dataset.frame)
            outcome.plan = plan
            outcome.execution = execution
            after_data = trace.last
            for step in execution.outcomes:
                plan_step = next(s for s in plan.steps if s.id == step.step_id)
                trace.add(
                    StepRecord(
                        agent="econometrician",
                        kind="tool",
                        status=_STEP_STATE[step.status],
                        parent=after_data,
                        tool=step.tool,
                        tool_call_hash=tool_call_hash(step.tool, plan_step.params),
                        detail=step.error or "; ".join(v.detail for v in step.refusals),
                    )
                )
                yield RunEvent(
                    name="step.finished",
                    detail=f"{step.step_id} ({step.tool}): {step.status}",
                    payload=step.model_dump(mode="json"),
                )

            async for event in self._run_code_steps(
                plan, dataset.frame, execution, outcome, trace, parent=after_data
            ):
                yield event

            outcome.diagnostics = _diagnostics_for(execution)
            outcome.charts = _charts_for(execution)
            yield RunEvent(
                name="charts.finished",
                detail=f"{len(outcome.charts)} chart(s)",
                payload={"charts": [chart.model_dump(mode="json") for chart in outcome.charts]},
            )

            validator = self._reviewer()
            if validator is None:
                break

            review = await validator.review(
                plan, execution, diagnostics=outcome.diagnostics
            )
            trace.add_agent_turn(
                review,
                agent="validator",
                provider=getattr(validator.provider, "name", None),
                model=validator.model,
                parent=trace.last,
            )
            verdict = review.output
            outcome.verdict = verdict
            yield RunEvent(
                name="validate.finished",
                detail="approved" if verdict.approved else "rejected",
                payload=verdict.model_dump(mode="json"),
            )

            if verdict.approved or outcome.revisions >= self.max_revisions:
                break

            outcome.revisions += 1
            revision_context = _revision_context(context, verdict)
            yield RunEvent(
                name="plan.revising",
                detail=f"revision {outcome.revisions} of {self.max_revisions}",
            )
            replan = await self.planners[0].plan(question, context=revision_context)
            trace.add_agent_turn(
                replan,
                agent="planner",
                provider=getattr(self.planners[0].provider, "name", None),
                model=self.planners[0].model,
                parent=trace.last,
            )
            previous = plan.dataset
            plan = replan.output

            # A revision may ask for a different window, and running it against
            # the previous plan's frame would make the recorded plan a wrong
            # account of its own numbers — `plan.dataset` naming one window
            # while the results came from another. Re-running such a plan from
            # its manifest disagrees, and rightly so. Unchanged specs are not
            # re-fetched: most revisions change the method, not the data.
            if plan.dataset != previous:
                dataset = await self.steward.resolve(plan.dataset)
                outcome.quality = dataset.report
                trace.add(
                    StepRecord(
                        agent="data_steward",
                        kind="tool",
                        status="ok",
                        parent=trace.last,
                        detail=f"{dataset.report.rows} rows, {len(dataset.report.flags)} flag(s)",
                    )
                )
                yield RunEvent(
                    name="data.finished", payload=dataset.report.model_dump(mode="json")
                )

        narration = await self.narrator.write(
            plan, execution, verdict=outcome.verdict
        )
        trace.add_agent_turn(
            narration,
            agent="narrator",
            provider=getattr(self.narrator.provider, "name", None),
            model=self.narrator.model,
            parent=trace.last,
            final_status="ok" if narration.published else "refused",
            final_detail="" if narration.published else narration.grounding.summary(),
        )
        outcome.narration = narration
        yield RunEvent(
            name="narrate.finished",
            detail="published" if narration.published else "withheld",
            payload=narration.model_dump(mode="json"),
        )

        # A rejection is information, not a failure. Only the grounding gate
        # withholding the prose downgrades the run.
        outcome.status = "completed" if narration.published else "blocked"

    # --- internals ----------------------------------------------------------

    def _check_code_permission(self, plan: AnalysisPlan) -> None:
        """The three conditions §2 puts on generated code, checked together.

        Two of them are `check_permitted`'s; the third is having a Quant Coder
        wired at all. All three refuse rather than degrade — see the call site.
        """
        if not plan.uses_generated_code():
            return
        check_permitted(enabled=self.code_sandbox, tier=self.tier)
        if self.coder is None:
            raise SandboxNotPermittedError(
                "the plan asks for generated code but no quant coder is configured"
                " for this run"
            )

    async def _run_code_steps(
        self,
        plan: AnalysisPlan,
        frame: Any,
        execution: ExecutionReport,
        outcome: RunOutcome,
        trace: TraceBuilder,
        *,
        parent: int | None,
    ) -> AsyncIterator[RunEvent]:
        """The escape hatch, after every registry step has finished.

        Ordered after them deliberately: `AnalysisPlan` refuses a tool step
        that waits on a code step, so by the time any of this runs the
        registry's own results are complete and a code step's dependencies are
        all settled.
        """
        if not plan.code_steps or self.coder is None:
            return

        produced = {step.step_id for step in execution.outcomes if step.status == "ran"}
        for step in plan.ordered_code_steps():
            upstream = sorted(set(step.depends_on) - produced)
            if upstream:
                execution.outcomes.append(
                    StepOutcome(
                        step_id=step.id,
                        tool="sandbox",
                        status="skipped",
                        error=f"depends on {', '.join(upstream)},"
                        " which did not produce a result",
                    )
                )
                continue

            run = await self.coder.compute(step.intent, frame, context=plan.question)
            trace.add_agent_turn(
                run,
                agent="quant_coder",
                provider=getattr(self.coder.provider, "name", None),
                model=self.coder.model,
                parent=parent,
                final_status="ok" if run.published else "refused",
                final_detail=run.error,
            )

            status = _code_status(run.published, run.status)
            tool = run.result.tool if run.result is not None else "sandbox"
            trace.add(
                StepRecord(
                    agent="quant_coder",
                    kind="tool",
                    status=_STEP_STATE[status],
                    parent=trace.last,
                    tool=tool,
                    tool_call_hash=tool_call_hash(tool, {"intent": step.intent}),
                    detail=run.error or f"{len(run.denials)} denial(s)",
                )
            )
            execution.outcomes.append(
                StepOutcome(
                    step_id=step.id,
                    tool=tool,
                    status=status,
                    result=run.result,
                    error=run.error,
                )
            )
            if run.published:
                produced.add(step.id)
                # A reader must not have to notice a tool name to learn that a
                # number came from code a model wrote this morning.
                outcome.warnings.append(
                    f"step {step.id} used an unvalidated method: {run.method!r} was"
                    " computed by generated code in the sandbox, not by a registry"
                    " tool, and carries no tested implementation or version"
                )
            yield RunEvent(
                name="step.finished",
                detail=f"{step.id} ({tool}): {status}",
                payload=execution.outcomes[-1].model_dump(mode="json"),
            )

    def _reviewer(self) -> Validator | None:
        """The Validator, if this tier uses one.

        The tier decides, not the wiring. A project set to `single` gets no
        review even when a Validator is configured — otherwise "cheapest tier"
        would silently depend on how the orchestrator happened to be built.
        """
        return None if self.tier == "single" else self.validator

    def _warn_about_independence(self, outcome: RunOutcome) -> None:
        if self._reviewer() is None:
            return
        reviewer = self._reviewer()
        assert reviewer is not None  # guarded above
        warning = independence_warning(
            author=getattr(self.planners[0].provider, "name", None),
            validator=getattr(reviewer.provider, "name", None),
        )
        if warning:
            outcome.warnings.append(warning)

    async def _search_context(self, question: str, context: str, trace: TraceBuilder) -> str:
        """Web results as extra context for the Planner, or the context unchanged.

        The Planner is the agent that benefits: it picks tickers and a window
        out of prose, and the failures this exists to reduce are exactly that —
        a Planner invented `LON` for London real estate and `NSEI` for the Nifty
        50 (the real symbol is `^NSEI`), and both runs died in the Data Steward.

        The verbatim question is a poor query for that — it returns commentary,
        not a symbol — so a query writer turns it into symbol-shaped lookups
        first. Deliberately not offered to the Narrator: its output is what the
        grounding gate judges, the gate withholds a whole narration over one
        number it cannot match, and web snippets are dense with numbers.
        """
        if not (self.web_search and self.searcher is not None):
            return context

        blocks: list[str] = []
        for query in await self._search_queries(question, trace):
            outcome = await search(query, provider=self.searcher, enabled=self.web_search)
            record = outcome.to_step_record()
            # The fields 6.10 added to answer "what was the model actually
            # shown" — here the query it ran and the results it found.
            record.prompt = query[:PROMPT_LIMIT]
            record.response = outcome.as_context()[:PROMPT_LIMIT]
            trace.add(record)
            found = outcome.as_context()
            if found:
                blocks.append(found)

        if not blocks:
            # A failed or empty search leaves the context alone rather than
            # appending a bare header, which would tell the model a search ran
            # and found nothing — a different claim from no search at all.
            return context
        combined = "\n\n".join(blocks)
        return f"{context}\n\n{combined}" if context else combined

    async def _search_queries(self, question: str, trace: TraceBuilder) -> list[str]:
        """The queries to search: model-written when a writer is configured,
        the verbatim question otherwise.

        A writer that refuses or cannot produce a usable reply falls back to the
        question. Search is context; losing it — or the whole run — to a query
        writer that would not answer is the worse trade.
        """
        if self.query_writer is None:
            return [question]
        try:
            result = await self.query_writer.write(question)
        except (AgentAttemptsExhaustedError, AgentRefusedError):
            return [question]

        trace.add_agent_turn(
            result,
            agent="query_writer",
            provider=getattr(self.query_writer.provider, "name", None),
            model=self.query_writer.model,
            parent=trace.last,
        )
        return result.output.queries[:MAX_SEARCH_QUERIES] or [question]

    async def _plan(
        self, question: str, context: str, outcome: RunOutcome, trace: TraceBuilder
    ) -> AnalysisPlan:
        def record(result: Any, planner: Planner) -> None:
            trace.add_agent_turn(
                result,
                agent="planner",
                provider=getattr(planner.provider, "name", None),
                model=planner.model,
                parent=None,
            )

        if self.tier != "consensus" or len(self.planners) == 1:
            result = await self.planners[0].plan(question, context=context)
            record(result, self.planners[0])
            return result.output

        plans = []
        for planner in self.planners:
            # Every planner's turn is traced, not only the one that was run:
            # a consensus tier that hid what the others cost would misreport
            # the price of the tier.
            result = await planner.plan(question, context=context)
            record(result, planner)
            plans.append(result.output)
        signatures = {_signature(plan) for plan in plans}
        if len(signatures) > 1:
            # Surfaced, never arbitrated. Two defensible approaches to the same
            # question is a finding about the question, and picking one
            # silently would hide it.
            outcome.alternative_plans = plans[1:]
            outcome.warnings.append(
                f"the planners disagree: {len(signatures)} distinct approaches were"
                f" proposed ({'; '.join(sorted(signatures))}). The first is being"
                " run; the others are reported for comparison."
            )
        return plans[0]


def _code_status(published: bool, sandbox_status: str) -> str:
    """A refused escape attempt is not a crash.

    The Econometrician already draws this line for precondition gates —
    `refused` means the system declined, `failed` means something broke — and
    a reader of the trace needs the same distinction here most of all.
    """
    if published:
        return "ran"
    return "refused" if sandbox_status == "denied" else "failed"


def _signature(plan: AnalysisPlan) -> str:
    """A plan's identity for comparison: which tools, in which order."""
    return " -> ".join(step.tool for step in plan.ordered_steps())


def _revision_context(context: str, verdict: ValidationVerdict) -> str:
    parts = [context] if context else []
    parts.append(
        "A previous plan was rejected by the Validator for these reasons:\n"
        + "\n".join(f"- {reason}" for reason in verdict.reasons)
    )
    if verdict.revise_steps:
        parts.append(f"Steps needing rework: {', '.join(verdict.revise_steps)}")
    return "\n\n".join(parts)


def _charts_for(execution: ExecutionReport) -> list[ChartSpec]:
    """What each result supports being drawn as.

    Deterministic, and stays that way here. `charts/propose.py` decides from a
    result's shape, which is settled — asking a model to rediscover it per run
    buys nothing. The `Visualizer` remains available for the editorial pass it
    was built for (ordering and retitling), but it curates one result per turn,
    so wiring it in unconditionally would put a model call behind every result
    a run produced. That is a cost the canvas should choose, not the pipeline.

    A step that refused or failed has no result, so it contributes no charts:
    drawing something for it would show an analysis that did not happen.
    """
    charts: list[ChartSpec] = []
    for step_id, result in execution.results.items():
        # `propose_charts` knows the result, not the plan, so the citation is
        # attached here — it is what lets a chart be traced back and re-run.
        charts.extend(
            chart.model_copy(update={"step_id": step_id}) for chart in propose_charts(result)
        )
    return charts


def _diagnostics_for(execution: ExecutionReport) -> list[Diagnostic]:
    """The deterministic battery over whatever residuals the run produced.

    Tools that expose a residual series get the full assumption check; the
    rest contribute the diagnostics they computed themselves. Either way the
    Validator is handed numbers rather than asked to imagine them.
    """
    collected: list[Diagnostic] = []
    for outcome in execution.outcomes:
        if outcome.result is None:
            continue
        collected.extend(outcome.result.diagnostics)
        residuals = outcome.result.series.get("residuals")
        if residuals is None:
            continue
        values = [value for value in residuals.y if value is not None]
        try:
            collected.extend(run_diagnostics(_as_series(values)))
        except Exception:
            # A battery that cannot run is not a reason to lose the run; the
            # tool's own diagnostics are already collected above.
            continue
    return collected


def _as_series(values: list[float]) -> Any:
    import pandas as pd

    return pd.Series(values, dtype=float)
