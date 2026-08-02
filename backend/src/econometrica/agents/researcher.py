"""The Research agent: a bounded tool-calling loop over MCP tools.

Not an `Agent[T]` — that base returns one validated JSON object. This is a
free-form conversation that ends in prose: the model is offered the project's
allowlisted MCP tools, calls what it chooses, sees the results, and eventually
answers without a tool call. Its answer is *context* for the Planner, read not
computed — the grounding gate blocks any number quoted from it, exactly as it
does for web search and retrieval.

`agents/` imports no `db.models` and no MCP SDK transport type: this speaks
`McpClient` and a `Connector`, both from `mcp/`.
"""

import re
from dataclasses import dataclass, field

from econometrica.agents.trace import StepRecord
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Completion, Message, ToolCall, ToolSpec
from econometrica.mcp.allowlist import ToolNotAllowedError
from econometrica.mcp.client import McpCall, McpClient, McpUnavailableError
from econometrica.mcp.connect import Connector

MAX_RESEARCH_ROUNDS = 4

#: Model tool names must be `[A-Za-z0-9_-]+`, but the allowlist is `server:tool`.
#: Offered names are `server__tool`; a map reconstructs the call, so the exact
#: spelling never has to round-trip.
_SEPARATOR = "__"

_SYSTEM = """\
You are the Researcher in an econometrics workbench. Before an analysis is
planned, you may call the tools below to gather context the Planner needs —
a data definition, a ticker, a methodology note, an internal figure to look up.

Call a tool when it helps. When you have what you need, reply with a short plain
summary of what you found and stop calling tools. Nothing you return is a
computed result; it is context read from a tool, and a later agent decides what
to compute.
"""

_SUMMARISE = "Summarise what you found, in a few sentences. Do not call any tool."


@dataclass
class ResearchOutcome:
    summary: str = ""
    calls: list[McpCall] = field(default_factory=list)
    records: list[StepRecord] = field(default_factory=list)
    failed: bool = False
    detail: str = ""

    def as_context(self) -> str:
        if not self.summary.strip():
            return ""
        return (
            "# From MCP tools — read, not computed.\n"
            "Nothing here is a result. Do not cite a number from it.\n\n"
            + self.summary
        )


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


class Researcher:
    role = "researcher"

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        connector: Connector,
        *,
        max_rounds: int = MAX_RESEARCH_ROUNDS,
    ) -> None:
        self.provider = provider
        self.model = model
        self._connector = connector
        self._max_rounds = max_rounds

    async def research(self, question: str) -> ResearchOutcome:
        outcome = ResearchOutcome()
        async with self._connector.open() as clients:
            offered, index = await self._offer(clients, outcome)
            if not offered:
                # Nothing allowed or reachable: no research to do.
                return outcome

            messages = [Message.system(_SYSTEM), Message.user(f"Question: {question}")]
            for _ in range(self._max_rounds):
                completion = await self.provider.complete(
                    messages, model=self.model, tools=offered
                )
                outcome.records.append(self._llm_step(completion))
                if not completion.tool_calls:
                    outcome.summary = completion.content
                    return outcome

                messages.append(Message.assistant(completion.content, completion.tool_calls))
                for call in completion.tool_calls:
                    result = await self._run(call, index, outcome)
                    messages.append(Message.tool_result(call.id, result))

            # Hit the cap while still calling tools: one tool-free summary call.
            messages.append(Message.user(_SUMMARISE))
            final = await self.provider.complete(messages, model=self.model)
            outcome.records.append(self._llm_step(final))
            outcome.summary = final.content
        return outcome

    async def _offer(
        self, clients: list[McpClient], outcome: ResearchOutcome
    ) -> tuple[list[ToolSpec], dict[str, tuple[McpClient, str]]]:
        """The allowlisted tools, as ToolSpecs, plus the map back to (client, tool)."""
        specs: list[ToolSpec] = []
        index: dict[str, tuple[McpClient, str]] = {}
        for client in clients:
            try:
                discovered = await client.discover()
            except McpUnavailableError as exc:
                outcome.detail = str(exc)
                continue
            for tool in discovered:
                if not tool.allowed:
                    continue
                offered_name = f"{_safe(client.server)}{_SEPARATOR}{_safe(tool.name)}"
                specs.append(
                    ToolSpec(
                        name=offered_name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                )
                index[offered_name] = (client, tool.name)
        return specs, index

    async def _run(
        self,
        call: ToolCall,
        index: dict[str, tuple[McpClient, str]],
        outcome: ResearchOutcome,
    ) -> str:
        """Execute one requested call, or turn its failure into an error result."""
        target = index.get(call.name)
        if target is None:
            return f"error: {call.name!r} is not an available tool"
        client, tool = target
        try:
            result = await client.call(tool, call.arguments)
        except (McpUnavailableError, ToolNotAllowedError) as exc:
            # Fed back to the model, not raised: the loop adapts, the run survives.
            return f"error: {exc}"
        outcome.calls.append(result)
        outcome.records.append(result.to_step_record())
        return result.output

    def _llm_step(self, completion: Completion) -> StepRecord:
        return StepRecord(
            agent="researcher",
            kind="llm",
            status="ok",
            provider=getattr(self.provider, "name", None),
            model=self.model,
            usage=completion.usage,
            latency_ms=completion.latency_ms,
            response=completion.content,
        )
