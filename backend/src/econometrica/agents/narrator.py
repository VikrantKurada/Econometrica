"""The Narrator: results become an interpretation a person can read.

The grounding gate is enforced here rather than hoped for. A draft citing a
number nothing computed is rejected before it is ever returned, re-asked once
with the offending figures named, and — if the second draft is no better —
withheld entirely. The orchestrator gets a report saying why instead of prose
that reads perfectly and is wrong.

Withholding is the right failure. Results without prose are inconvenient;
prose with an invented statistic is the thing this whole application exists to
prevent.
"""

from pydantic import BaseModel, Field

from econometrica.agents.base import Agent, AgentAttemptsExhaustedError
from econometrica.agents.econometrician import ExecutionReport
from econometrica.agents.grounding import GroundingReport, allowed_values, check_grounding
from econometrica.agents.schemas import AnalysisPlan, ValidationVerdict
from econometrica.econ.types import ResultSet
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message

_SYSTEM = """\
You are the Narrator in an econometrics workbench. The tools have run. Your
job is to say what the results mean, for a reader who understands finance but
did not watch the analysis happen.

Every number you write is checked against the computed results before anything
is shown to anyone. A figure that does not match one of them is not a stylistic
problem — the whole reply is withheld. So:

- Cite only numbers that appear in the results below, rounded if you like.
- Never estimate, extrapolate or infer a figure that was not computed.
- If a step was refused or a check could not be judged, say so plainly. A
  hedge a reader can act on beats a confident sentence that is unearned.

Reply with a single JSON object and nothing else:

{"prose": "the interpretation, in markdown",
 "citations": ["ids of the steps you drew on"]}\
"""


class Narrative(BaseModel):
    prose: str = Field(min_length=1)
    #: Plan step ids the prose draws on.
    citations: list[str] = Field(default_factory=list)


class Narration(BaseModel):
    """What the Narrator produced, and whether it may be shown.

    A model rather than a dataclass so it can travel over the run's SSE
    stream unchanged — the grounding report is exactly what a client needs to
    explain a withheld interpretation.
    """

    published: bool
    narrative: Narrative | None = None
    grounding: GroundingReport


class Narrator(Agent[Narrative]):
    role = "narrator"

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)
        self._allowed: set[float] = set()
        self._known_steps: frozenset[str] = frozenset()
        self._grounding = GroundingReport(grounded=True)

    def output_model(self) -> type[Narrative]:
        return Narrative

    def check(self, output: Narrative) -> None:
        invented = sorted(set(output.citations) - self._known_steps)
        if invented:
            raise ValueError(
                f"citations names step(s) not in the plan: {', '.join(invented)}."
                f" Valid ids: {', '.join(sorted(self._known_steps))}"
            )

        self._grounding = check_grounding(output.prose, self._allowed)
        if not self._grounding.grounded:
            # Naming the figures is what makes the retry worth spending: a
            # bare "that was ungrounded" gets the same draft back.
            raise ValueError(f"ungrounded numbers — {self._grounding.summary()}")

    async def write(
        self,
        plan: AnalysisPlan,
        report: ExecutionReport,
        *,
        verdict: ValidationVerdict | None = None,
    ) -> Narration:
        results = list(report.results.values())
        self._allowed = allowed_values(results)
        self._known_steps = frozenset(step.id for step in plan.steps)
        self._grounding = GroundingReport(grounded=True)

        try:
            result = await self.ask(
                [
                    Message.system(_SYSTEM),
                    Message.user(_render(plan, report, verdict)),
                ]
            )
        except AgentAttemptsExhaustedError:
            # Every draft failed. The last grounding report is the useful part
            # — it says which figures were invented.
            return Narration(published=False, narrative=None, grounding=self._grounding)

        return Narration(published=True, narrative=result.output, grounding=self._grounding)


# --- rendering --------------------------------------------------------------


def _render(
    plan: AnalysisPlan, report: ExecutionReport, verdict: ValidationVerdict | None
) -> str:
    blocks = [
        f"Question: {plan.question}",
        f"Data: {', '.join(plan.dataset.tickers)} "
        f"{plan.dataset.start}..{plan.dataset.end} "
        f"({plan.dataset.frequency}, {plan.dataset.return_method} returns)",
    ]

    if plan.hypotheses:
        blocks.append("Hypotheses: " + "; ".join(plan.hypotheses))

    blocks.append("\n# Results")
    for outcome in report.outcomes:
        head = f"\n## {outcome.step_id} — {outcome.tool} ({outcome.status})"
        if outcome.error:
            head = f"{head}\n{outcome.error}"
        blocks.append(head)
        if outcome.result is not None:
            blocks.append(_result_block(outcome.result))
        for refusal in outcome.refusals:
            blocks.append(f"Refused: {refusal.detail}")

    if report.unjudged:
        blocks.append(
            "\n# Checks that could not be judged — disclose these\n"
            + "\n".join(f"- {v.detail}" for v in report.unjudged)
        )

    if verdict is not None:
        state = "approved" if verdict.approved else "rejected"
        blocks.append(f"\n# Validator verdict: {state}")
        blocks.extend(f"- {reason}" for reason in verdict.reasons)

    return "\n".join(blocks)


def _result_block(result: ResultSet) -> str:
    lines: list[str] = []
    for estimate in result.estimates:
        parts = [f"{estimate.name} = {estimate.value:.6g}"]
        if estimate.std_error is not None:
            parts.append(f"se {estimate.std_error:.6g}")
        if estimate.p_value is not None:
            parts.append(f"p {estimate.p_value:.6g}")
        lines.append("- " + ", ".join(parts))
    for name, value in result.scalars.items():
        lines.append(f"- {name} = {value:.6g}")
    for diagnostic in result.diagnostics:
        verdict = {True: "passed", False: "failed", None: "not judged"}[diagnostic.passed]
        line = f"- {diagnostic.name}: statistic {diagnostic.statistic:.6g}"
        if diagnostic.p_value is not None:
            line += f", p-value {diagnostic.p_value:.6g}"
        lines.append(f"{line}, {verdict}")
    return "\n".join(lines)
