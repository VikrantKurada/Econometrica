"""The Validator: a second opinion, on facts rather than impressions.

Two things make this role worth having, and both are structural rather than a
matter of prompting well.

**It is fed, not asked.** A deterministic diagnostics engine runs first, and
its statistics go into the prompt as numbers. An LLM asked to judge from prose
whether residuals are heteroskedastic will produce a confident answer either
way; one handed an ARCH-LM statistic and its p-value is doing a different and
much smaller job.

**It runs on a different vendor.** A model reviewing reasoning from its own
family shares its blind spots, so :func:`independence_warning` exists and the
orchestrator surfaces it.
"""

from econometrica.agents.base import Agent, AgentResult
from econometrica.agents.econometrician import ExecutionReport
from econometrica.agents.schemas import AnalysisPlan, ValidationVerdict
from econometrica.econ.types import Diagnostic, ResultSet
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message

_SYSTEM = """\
You are the Validator in an econometrics workbench. Another agent chose the
methods and the tools computed the results. Your job is to say whether the
work supports its conclusions.

You are given the deterministic diagnostics as numbers. Do not re-derive them,
do not estimate anything, and do not invent statistics that are not below.

Judge:
- whether each method suits the data it was run on
- whether the diagnostics undermine the estimates
- whether refused steps leave the question unanswered
- whether checks that could not be judged matter to the conclusion

Reply with a single JSON object and nothing else:

{"approved": true|false,
 "reasons": ["why, in specific terms"],
 "revise_steps": ["ids of steps that need reworking"]}

`revise_steps` may only name step ids from the plan. A rejection must give at
least one reason; a reason a reader cannot act on is not a reason.\
"""


def independence_warning(*, econometrician: str | None, validator: str | None) -> str | None:
    """Warn when the review is not actually independent.

    Same provider means shared training data and shared blind spots, which is
    not a second opinion however the prompt is worded. ``None`` on either side
    means that role runs no model at all, so there is nothing to be marked
    against.
    """
    if econometrician is None or validator is None:
        return None
    if econometrician != validator:
        return None
    return (
        f"the Validator and the Econometrician are both on {econometrician}:"
        " a model reviewing reasoning from its own family shares its blind"
        " spots. Assign the Validator a different provider."
    )


class Validator(Agent[ValidationVerdict]):
    role = "validator"

    def output_model(self) -> type[ValidationVerdict]:
        return ValidationVerdict

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)
        #: The plan's step ids, set for the duration of one review. An
        #: instance reviews one run at a time, which is how the orchestrator
        #: uses it.
        self._known_steps: frozenset[str] = frozenset()

    def check(self, output: ValidationVerdict) -> None:
        invented = sorted(set(output.revise_steps) - self._known_steps)
        if invented:
            raise ValueError(
                f"revise_steps names step(s) that are not in the plan:"
                f" {', '.join(invented)}."
                f" Valid ids: {', '.join(sorted(self._known_steps))}"
            )

    async def review(
        self,
        plan: AnalysisPlan,
        report: ExecutionReport,
        *,
        diagnostics: list[Diagnostic] | None = None,
    ) -> AgentResult[ValidationVerdict]:
        self._known_steps = frozenset(step.id for step in plan.steps)
        return await self.ask(
            [
                Message.system(_SYSTEM),
                Message.user(_render(plan, report, diagnostics or [])),
            ]
        )


# --- rendering --------------------------------------------------------------


def _render(
    plan: AnalysisPlan, report: ExecutionReport, diagnostics: list[Diagnostic]
) -> str:
    blocks = [
        f"Question: {plan.question}",
        f"Data: {', '.join(plan.dataset.tickers)} "
        f"{plan.dataset.start}..{plan.dataset.end} "
        f"({plan.dataset.frequency}, {plan.dataset.return_method} returns)",
        "\n# Steps\n" + "\n".join(_step_lines(plan, report)),
    ]

    if report.refusals:
        blocks.append(
            "\n# Refused by precondition\n"
            + "\n".join(f"- {verdict.detail}" for verdict in report.refusals)
        )

    if report.unjudged:
        blocks.append(
            "\n# Not judged — these checks could not be evaluated\n"
            + "\n".join(f"- {verdict.detail}" for verdict in report.unjudged)
        )

    if diagnostics:
        blocks.append("\n# Deterministic diagnostics\n" + _diagnostic_lines(diagnostics))

    return "\n".join(blocks)


def _step_lines(plan: AnalysisPlan, report: ExecutionReport) -> list[str]:
    lines: list[str] = []
    for step in plan.steps:
        try:
            outcome = report.outcome(step.id)
        except KeyError:
            lines.append(f"- {step.id} ({step.tool}): not reached")
            continue

        head = f"- {step.id} ({step.tool}): {outcome.status}"
        if outcome.error:
            head = f"{head} — {outcome.error}"
        lines.append(head)
        if outcome.result is not None:
            lines.extend(f"    {line}" for line in _result_lines(outcome.result))
    return lines


def _result_lines(result: ResultSet) -> list[str]:
    lines: list[str] = []
    for estimate in result.estimates:
        parts = [f"{estimate.name} = {estimate.value:.6g}"]
        if estimate.std_error is not None:
            parts.append(f"se {estimate.std_error:.6g}")
        if estimate.p_value is not None:
            parts.append(f"p {estimate.p_value:.6g}")
        lines.append(", ".join(parts))
    for name, value in result.scalars.items():
        lines.append(f"{name} = {value:.6g}")
    lines.extend(_diagnostic_lines(result.diagnostics).splitlines())
    return lines


def _diagnostic_lines(diagnostics: list[Diagnostic]) -> str:
    """Every diagnostic as a number, with its verdict spelled out.

    `passed` is tri-state and the word for ``None`` is "not judged" — never
    "failed". A model told a check failed when it merely did not run will
    reject work for a reason that does not exist.
    """
    lines = []
    for diagnostic in diagnostics:
        verdict = {True: "passed", False: "failed", None: "not judged"}[diagnostic.passed]
        parts = [f"{diagnostic.name}: statistic {diagnostic.statistic:.6g}"]
        if diagnostic.p_value is not None:
            parts.append(f"p-value {diagnostic.p_value:.6g}")
        parts.append(verdict)
        if diagnostic.interpretation:
            parts.append(diagnostic.interpretation)
        lines.append("- " + ", ".join(parts))
    return "\n".join(lines)
