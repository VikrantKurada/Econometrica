"""The Planner: a question in prose becomes an `AnalysisPlan`.

It chooses *what to do*, never *what the answer is*. Every step it emits names
a registry tool and carries parameters that tool's own model accepts, both
enforced by `PlanStep` — so the worst a confused Planner can produce is a
rejected plan and a retry, never a wrong number.
"""

from datetime import date

from econometrica.agents.base import Agent, AgentResult
from econometrica.agents.catalogue import render_tool_catalogue
from econometrica.agents.schemas import AnalysisPlan
from econometrica.llm.types import Message

_SYSTEM = """\
You are the Planner in an econometrics workbench.

You do not compute statistics and you do not state findings. You choose which
of the tools below to run, in what order, on what data. The tools compute; a
later agent interprets what they produce.

Reply with a single JSON object and nothing else:

{{
  "question": "the user's question, restated",
  "dataset": {{
    "tickers": ["TICKER"],
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD",
    "frequency": "D|W|M|Q|A",
    "return_method": "log|simple",
    "risk_free": null
  }},
  "steps": [
    {{"id": "s1", "tool": "<name from the catalogue>", "params": {{}},
      "depends_on": [], "rationale": "why this step"}}
  ],
  "hypotheses": ["what the analysis would show if true"],
  "chart_intents": ["what a reader should be able to see"]
}}

Rules:
- `tool` must be a name from the catalogue. Never invent one.
- `params` may only use parameters the catalogue lists for that tool. Omit any
  you do not need; every one has a default.
- `depends_on` holds ids of earlier steps. The steps must form a DAG.
- At least one step. If the question cannot be answered with these tools, say
  so in `hypotheses` and plan the closest thing that can be.

# Tool catalogue
{catalogue}\
"""


class Planner(Agent[AnalysisPlan]):
    """Turns intent plus project context into a validated `AnalysisPlan`."""

    role = "planner"

    def output_model(self) -> type[AnalysisPlan]:
        return AnalysisPlan

    async def plan(
        self,
        question: str,
        *,
        context: str = "",
        today: date | None = None,
    ) -> AgentResult[AnalysisPlan]:
        """Plan an analysis for ``question``.

        ``today`` is passed explicitly rather than read from the clock: a
        model asked for "the last five years" needs to know what now is, and a
        plan whose window depends on an implicit clock is not reproducible.
        """
        return await self.ask(
            [
                Message.system(_SYSTEM.format(catalogue=render_tool_catalogue())),
                Message.user(self._request(question, context, today)),
            ]
        )

    @staticmethod
    def _request(question: str, context: str, today: date | None) -> str:
        parts = [f"Question: {question}"]
        if context:
            parts.append(f"Project context: {context}")
        if today is not None:
            parts.append(f"Today's date: {today.isoformat()}")
        return "\n\n".join(parts)
