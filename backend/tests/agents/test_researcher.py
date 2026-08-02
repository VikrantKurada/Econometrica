"""The research loop: a model calls allowlisted MCP tools, then summarises."""

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from econometrica.agents.researcher import Researcher
from econometrica.llm.types import Completion, ToolCall, Usage
from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.client import McpClient

EXECUTED: list[str] = []


def build_server() -> FastMCP:
    server = FastMCP("facts")

    @server.tool()
    def lookup(symbol: str) -> str:
        """Look up a symbol."""
        EXECUTED.append(f"lookup:{symbol}")
        return f"{symbol} is the Nifty 50 index"

    @server.tool()
    def wipe() -> str:
        """Offered, never allowed."""
        EXECUTED.append("wipe")
        return "wiped"

    return server


class ScriptedModel:
    """Returns pre-built completions in order and records the tools it was given."""

    name = "scripted"

    def __init__(self, completions: list[Completion]) -> None:
        self._completions = completions
        self.tools_seen: list[list[str]] = []
        self._i = 0

    async def complete(self, messages, *, model, tools=None, **kw) -> Completion:
        self.tools_seen.append([t.name for t in (tools or [])])
        completion = self._completions[self._i]
        self._i += 1
        return completion


def tool_call(name, args, id="c1") -> Completion:
    return Completion(tool_calls=[ToolCall(id=id, name=name, arguments=args)], usage=Usage())


def text(content) -> Completion:
    return Completion(content=content, usage=Usage())


class OneServerConnector:
    """Yields a McpClient over an in-memory FastMCP, with the given allowlist."""

    def __init__(self, allowlist: list[str]) -> None:
        self._allowlist = Allowlist(allowlist)

    def open(self):
        @asynccontextmanager
        async def _open():
            async with create_connected_server_and_client_session(build_server()) as session:
                yield [McpClient(session=session, server="facts", allowlist=self._allowlist)]

        return _open()


def setup_function():
    EXECUTED.clear()


async def test_the_model_calls_an_allowed_tool_and_summarises():
    model = ScriptedModel(
        [tool_call("facts__lookup", {"symbol": "^NSEI"}), text("The symbol is the Nifty 50.")]
    )
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    outcome = await researcher.research("What is ^NSEI?")

    assert EXECUTED == ["lookup:^NSEI"]
    assert "Nifty 50" in outcome.summary
    assert outcome.failed is False
    assert "read, not computed" in outcome.as_context().lower()


async def test_only_allowlisted_tools_are_offered_to_the_model():
    model = ScriptedModel([text("nothing to do")])
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    await researcher.research("q")

    # `wipe` exists on the server but is not allowlisted, so it is never offered.
    assert model.tools_seen[0] == ["facts__lookup"]


async def test_a_hallucinated_tool_is_fed_back_as_an_error_not_run():
    model = ScriptedModel(
        [tool_call("facts__wipe", {}), text("could not do that")]
    )
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    outcome = await researcher.research("q")

    assert EXECUTED == []  # the disallowed tool never ran
    assert "could not do that" in outcome.summary


async def test_the_loop_stops_at_the_round_cap_and_still_summarises():
    # A tool call every round up to the cap (3), then the tool-free summary call
    # the loop makes on hitting the cap returns the text.
    calls = [tool_call("facts__lookup", {"symbol": "x"}, id=f"c{i}") for i in range(3)]
    model = ScriptedModel([*calls, text("here is what I found")])
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]), max_rounds=3)

    outcome = await researcher.research("q")

    assert "what I found" in outcome.summary
    assert len(outcome.calls) == 3  # bounded by max_rounds
    # The final summary call is made with no tools offered.
    assert model.tools_seen[-1] == []


async def test_a_run_that_calls_nothing_returns_its_text_in_one_round():
    model = ScriptedModel([text("no external data needed")])
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    outcome = await researcher.research("q")

    assert outcome.summary == "no external data needed"
    assert outcome.calls == []
