"""The Econometrician: a validated plan becomes results, or a refusal.

**Deterministic, like the Data Steward.** By the time a plan reaches here,
`PlanStep` has already proved every tool exists and every parameter is one it
accepts — so what remains is enforcing the gates and running the tools, and
neither is a question a model should be asked. The judgement in this phase
lives where judgement belongs: the Planner reading intent, the Validator
reading evidence, the Narrator writing prose.

A refused step is not an error. It is the system declining to produce a number
that would be taken seriously and should not be, which is the most valuable
thing it does.
"""

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from econometrica.agents.schemas import AnalysisPlan, PlanStep
from econometrica.econ.gates import PreconditionVerdict, evaluate_gates
from econometrica.econ.registry import RegisteredTool, get_registry
from econometrica.econ.types import ResultSet

StepStatus = Literal["ran", "refused", "failed", "skipped"]


class StepOutcome(BaseModel):
    """What became of one plan step."""

    step_id: str
    tool: str
    status: StepStatus
    result: ResultSet | None = None
    verdicts: list[PreconditionVerdict] = Field(default_factory=list)
    error: str = ""

    @property
    def refusals(self) -> list[PreconditionVerdict]:
        return [verdict for verdict in self.verdicts if verdict.refused]


class ExecutionReport(BaseModel):
    """Everything the run produced, including what it declined to produce."""

    outcomes: list[StepOutcome] = Field(default_factory=list)

    def outcome(self, step_id: str) -> StepOutcome:
        for outcome in self.outcomes:
            if outcome.step_id == step_id:
                return outcome
        raise KeyError(f"no step {step_id!r} in this report")

    @property
    def results(self) -> dict[str, ResultSet]:
        return {o.step_id: o.result for o in self.outcomes if o.result is not None}

    @property
    def refusals(self) -> list[PreconditionVerdict]:
        return [verdict for outcome in self.outcomes for verdict in outcome.refusals]

    @property
    def unjudged(self) -> list[PreconditionVerdict]:
        """Checks that could not be evaluated.

        Surfaced rather than buried: the Validator weighs these, and the
        Narrator has to disclose that an assumption went unchecked.
        """
        return [
            verdict
            for outcome in self.outcomes
            for verdict in outcome.verdicts
            if not verdict.judged
        ]


class Econometrician:
    """Runs the steps a plan asks for, and only those the data permits."""

    role = "econometrician"

    async def run(self, plan: AnalysisPlan, frame: pd.DataFrame) -> ExecutionReport:
        report = ExecutionReport()
        blocked: set[str] = set()

        for step in plan.ordered_steps():
            upstream = sorted(set(step.depends_on) & blocked)
            if upstream:
                # Running it would answer a question its refused dependency was
                # there to settle first.
                report.outcomes.append(
                    StepOutcome(
                        step_id=step.id,
                        tool=step.tool,
                        status="skipped",
                        error=f"depends on {', '.join(upstream)}, which did not produce a result",
                    )
                )
                blocked.add(step.id)
                continue

            outcome = self._execute(step, frame)
            report.outcomes.append(outcome)
            if outcome.status != "ran":
                blocked.add(step.id)

        return report

    # --- internals ----------------------------------------------------------

    def _execute(self, step: PlanStep, frame: pd.DataFrame) -> StepOutcome:
        tool = get_registry().get(step.tool)

        try:
            series = self._named_series(tool, step, frame)
        except KeyError as exc:
            return StepOutcome(
                step_id=step.id, tool=step.tool, status="failed", error=str(exc)
            )

        verdicts = evaluate_gates(tool, series)
        if any(verdict.refused for verdict in verdicts):
            return StepOutcome(
                step_id=step.id, tool=step.tool, status="refused", verdicts=verdicts
            )

        try:
            result = tool.fn(frame, tool.params_model.model_validate(step.params))
        except Exception as exc:
            # One bad step must not lose the results of the good ones, and the
            # Validator needs to see that it was attempted.
            return StepOutcome(
                step_id=step.id,
                tool=step.tool,
                status="failed",
                verdicts=verdicts,
                error=f"{type(exc).__name__}: {exc}",
            )

        return StepOutcome(
            step_id=step.id,
            tool=step.tool,
            status="ran",
            result=result,
            verdicts=verdicts,
        )

    @staticmethod
    def _named_series(
        tool: RegisteredTool, step: PlanStep, frame: pd.DataFrame
    ) -> dict[str, pd.Series]:
        """The columns this step operates on.

        Only two shapes exist across the registry — `column` for the
        single-series tools and `columns` for the multivariate ones — and only
        gated tools reach this code, so the convention does not have to hold
        for all 36. Defaults come from the params model, since a step that
        omits `column` still means the tool's default.
        """
        params = tool.params_model.model_validate(step.params)
        names: list[str] = []
        for field in ("column", "columns"):
            value = getattr(params, field, None)
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, list):
                names.extend(str(item) for item in value)

        missing = [name for name in names if name not in frame.columns]
        if missing:
            raise KeyError(
                f"{tool.name}: column(s) {', '.join(missing)} are not in the dataset"
                f" (available: {', '.join(map(str, frame.columns))})"
            )
        return {name: frame[name] for name in names}
