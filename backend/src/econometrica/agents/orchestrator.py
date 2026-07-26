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

from econometrica.agents.data_steward import DataQualityReport, DataSteward
from econometrica.agents.econometrician import Econometrician, ExecutionReport
from econometrica.agents.narrator import Narration, Narrator
from econometrica.agents.planner import Planner
from econometrica.agents.schemas import AnalysisPlan, ValidationVerdict
from econometrica.agents.trace import StepRecord, TraceBuilder, tool_call_hash
from econometrica.agents.validator import Validator, independence_warning
from econometrica.charts.propose import propose_charts
from econometrica.charts.spec import ChartSpec
from econometrica.econ.diagnostics.engine import run_diagnostics
from econometrica.econ.types import Diagnostic

Tier = Literal["single", "critic", "consensus"]
TIERS: tuple[Tier, ...] = ("single", "critic", "consensus")

RunStatus = Literal["completed", "blocked", "failed"]

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

        plan = await self._plan(question, context, outcome, trace)
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
