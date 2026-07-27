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
from econometrica.llm.base import LLMProvider
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
    "risk_free": null,
    "factors": null
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

# Column names

The data is assembled after you plan, and the columns are named from the
tickers you request — not "price" or "return", whatever a tool's default
says. For each ticker T there are exactly two columns:

- `T` — the price level. Use it for unit-root and variance-ratio work.
- `T_return` — the return, built with your `return_method`. Use it for
  volatility and autocorrelation work.

So for BTC-USD you would write `"column": "BTC-USD"` or
`"column": "BTC-USD_return"`. A column that is not one of these does not
exist, and the step will fail.

Two more columns appear only if you ask for them in `dataset`:

- `"risk_free": "DGS3MO"` adds a `risk_free` column of per-period risk-free
  returns, for tools that take a `risk_free` parameter. Use a FRED series id —
  `DGS3MO` (3-month Treasury) is the usual choice.
- `"factors": "ff3"` adds the Fama-French factor columns `mkt_rf`, `smb` and
  `hml`. `"ff5"` adds `rmw` and `cma` as well; `"carhart4"` gives `mkt_rf`,
  `smb`, `hml` and `mom`. **The `ff3`, `ff5` and `carhart4` tools cannot run
  without this** — their factor columns come from nowhere else. A factor set
  brings its own `risk_free` column, so do not set both.

`carhart4` is available at monthly frequency only.
{escape_hatch}
# Tool catalogue
{catalogue}\
"""

#: Appended only when the project has the code sandbox enabled. Omitted
#: otherwise, and that is not tidiness: a Planner told the field exists reaches
#: for it on a hard question, and every such plan would then be refused after
#: the model call had already been paid for. Off by default has to reach the
#: prompt, not only the executor.
_ESCAPE_HATCH = """
# When no tool fits

This project has the code sandbox enabled, so you may add a `code_steps` list
alongside `steps`:

  "code_steps": [
    {"id": "c1", "intent": "what to compute, in prose",
     "depends_on": ["s1"], "rationale": "why no tool fits"}
  ]

A later agent writes and runs code for each intent. Use it **only** when no
tool in the catalogue computes what is being asked for. Its results are marked
as an unvalidated method — no tested implementation, no version, no
precondition gate — and the reader is told so. Where a catalogue tool exists,
it is always the better answer.

Ids share one namespace with `steps`, so a `code_steps` id may not repeat an
`s` id. A `code_steps` entry may depend on a regular step; a regular step may
not depend on a `code_steps` entry.
"""


class Planner(Agent[AnalysisPlan]):
    """Turns intent plus project context into a validated `AnalysisPlan`."""

    role = "planner"

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        *,
        max_attempts: int = 2,
        code_sandbox: bool = False,
    ) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)
        #: Whether this project may use the escape hatch. Decides whether the
        #: prompt mentions `code_steps` at all — see `_ESCAPE_HATCH`.
        self.code_sandbox = code_sandbox

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
                Message.system(
                    _SYSTEM.format(
                        catalogue=render_tool_catalogue(),
                        escape_hatch=_ESCAPE_HATCH if self.code_sandbox else "",
                    )
                ),
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
