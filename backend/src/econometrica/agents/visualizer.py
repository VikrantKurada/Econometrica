"""The Visualizer: results become charts.

**The selection is deterministic; only the editing is a model's job.** What a
result supports is settled by its shape — `charts/propose.py` decides that —
and asking a model to rediscover it per run buys nothing and can get it wrong.
What a model is good at is the editorial pass: which of several defensible
charts leads, and what to call it for *this* question rather than in general.

So the role stays model-assignable, as the design intends, but its model can
only reorder, drop and retitle charts that already bind. It cannot invent one.
With no provider configured, call :func:`propose_charts` directly and skip the
turn entirely — a test covers that path, because it is the one most runs
should take.
"""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from econometrica.agents.base import Agent, AgentResult
from econometrica.charts.propose import propose_charts
from econometrica.charts.spec import unresolved_references
from econometrica.econ.types import ResultSet
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message

_SYSTEM = """\
You are the Visualizer in an econometrics workbench. The charts below have
already been chosen from the shape of the results and all of them are valid.
Your only job is editorial: order them so the most informative comes first,
drop any that add nothing for this question, and give each a title that says
what a reader should take from it.

You cannot add a chart, change its type, or change what data it draws. Those
are fixed.

Reply with a single JSON object and nothing else:

{"charts": [{"index": 0, "title": "…"}, {"index": 2, "title": "…"}]}

- `index` refers to the numbered list below. Every index must appear at most
  once, and at least one must appear.
- `title` may be empty to keep the existing one.\
"""


class ChartEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    title: str = ""


class ChartSelection(BaseModel):
    """Which proposed charts to show, in what order."""

    model_config = ConfigDict(extra="forbid")

    charts: list[ChartEdit] = Field(min_length=1)

    @model_validator(mode="after")
    def a_chart_appears_at_most_once(self) -> Self:
        seen = [edit.index for edit in self.charts]
        duplicates = sorted({index for index in seen if seen.count(index) > 1})
        if duplicates:
            raise ValueError(f"chart index(es) {duplicates} appear more than once")
        return self


class Visualizer(Agent[ChartSelection]):
    role = "visualizer"

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)
        self._count = 0

    def output_model(self) -> type[ChartSelection]:
        return ChartSelection

    def check(self, output: ChartSelection) -> None:
        out_of_range = sorted(
            edit.index for edit in output.charts if edit.index >= self._count
        )
        if out_of_range:
            raise ValueError(
                f"index(es) {out_of_range} are not in the list;"
                f" valid indices are 0..{self._count - 1}"
            )

    async def curate(
        self, result: ResultSet, question: str = ""
    ) -> tuple[list[Any], AgentResult[ChartSelection]]:
        """Order and retitle the charts this result supports."""
        proposed = propose_charts(result)
        if not proposed:
            raise ValueError(f"{result.tool} produced nothing that can be charted")

        self._count = len(proposed)
        selection = await self.ask(
            [Message.system(_SYSTEM), Message.user(_render(result, proposed, question))]
        )

        charts: list[Any] = []
        for edit in selection.output.charts:
            chart = proposed[edit.index].model_copy(
                update={"title": edit.title} if edit.title else {}
            )
            # A retitle cannot break a binding, but a future edit field could;
            # re-checking here keeps the invariant local to where it matters.
            assert unresolved_references(chart, result) == []
            charts.append(chart)
        return charts, selection


def _render(result: ResultSet, proposed: list[Any], question: str) -> str:
    lines = []
    if question:
        lines.append(f"Question: {question}")
    lines.append(f"Result from `{result.tool}` v{result.version}.")
    lines.append("\nCharts available:")
    for index, chart in enumerate(proposed):
        detail = f"{index}. [{chart.type}] {chart.title}"
        if chart.subtitle:
            detail += f" — {chart.subtitle}"
        lines.append(detail)
    return "\n".join(lines)
