"""The Query Writer: a question becomes symbol-shaped lookup queries.

Before an analysis is planned, this turns the user's prose into short web
searches whose only job is to surface the exact ticker or symbol of each
instrument the question names. The motivation is measured, not supposed: the
verbatim question "How has the National Stock Exchange of India grown…" returns
market commentary and no symbol, while "Nifty 50 ticker symbol Yahoo Finance"
returns `^NSEI` as the top hit. The extraction of "Nifty 50" from the prose is
the part no string transform can do and a model can, which is why this is a
billed turn.

It selects nothing numeric and states no finding — it writes queries. What its
queries find is fed to the Planner, never the Narrator, and is never a source of
numbers: the grounding gate is unchanged.
"""

from pydantic import BaseModel, Field, field_validator

from econometrica.agents.base import Agent, AgentResult
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message

_SYSTEM = """\
You are the Query Writer in an econometrics workbench. Before an analysis is
planned, you turn the user's question into short web-search queries whose only
job is to surface the exact ticker or symbol of each financial instrument the
question is about — an index, a stock, a currency pair, a commodity — in the
form a market-data vendor uses (for example ^NSEI, BTC-USD, ^GSPC).

A good query names the instrument by its common name and asks for its symbol,
e.g. "Nifty 50 ticker symbol Yahoo Finance" or "Brent crude oil Yahoo Finance
symbol". A whole analytical question ("how has it grown over ten years") is a
poor query: it returns commentary, not a symbol.

Write one query per distinct instrument the question names. If the question
already gives an explicit ticker, you may still write a query to confirm it.

Reply with a single JSON object and nothing else:

{"queries": ["<instrument name> ticker symbol Yahoo Finance"]}\
"""


class SearchQuery(BaseModel):
    """The queries to run, cleaned. Local to this module: it is consumed inside
    `_search_context` and never passed downstream, so it is not a cross-agent
    contract that belongs in `agents/schemas.py`."""

    queries: list[str] = Field(default_factory=list)

    @field_validator("queries", mode="after")
    @classmethod
    def clean(cls, value: list[str]) -> list[str]:
        # Strip, drop empties, de-duplicate case-insensitively. Raising when
        # nothing survives spends a retry rather than the run — the same path a
        # malformed reply takes, since the base loop treats a ValueError here
        # exactly as it treats a parse failure.
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        if not cleaned:
            raise ValueError("at least one non-empty search query is required")
        return cleaned


class QueryWriter(Agent[SearchQuery]):
    role = "query_writer"

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)

    def output_model(self) -> type[SearchQuery]:
        return SearchQuery

    async def write(self, question: str) -> AgentResult[SearchQuery]:
        return await self.ask(
            [Message.system(_SYSTEM), Message.user(f"Question: {question}")]
        )
